"""Fixed provider routes with one HTTPS attempt and a process wall deadline."""
from dataclasses import dataclass
from datetime import datetime,timezone
from urllib.parse import urlencode,quote
import http.client,multiprocessing,os,pickle,select,struct,time,socket
from ..credentials import read_project_credential,_validate_value
from ..errors import ValidationError,ResourceLimitError,StoreUnavailableError
from .collection_plan import validate_unit
from .collection_queue import QueueResponse,SAFE_HEADERS

@dataclass(frozen=True)
class RequestRoute:
    host: str
    path: str
    parameters: tuple[tuple[str,str],...]
    credential_names: tuple[str,...]
    credential_parameter: str | None
    credential_header: str | None = None

def request_route(unit):
    validate_unit(unit)
    params=dict(unit.parameters)
    if unit.provider=="fmp":
        if unit.collection=="news":raise ValidationError("News retains its own fixed source transport")
        if unit.collection=="daily_prices":allowed={"symbol","from","to"}
        elif unit.collection=="fmp_statements":allowed={"symbol","period","limit"}
        elif unit.endpoint=="analyst-estimates":allowed={"symbol","period","page","limit"}
        elif unit.endpoint=="revenue-product-segmentation":allowed={"symbol","period","limit"}
        elif unit.endpoint in ("grades-consensus","price-target-consensus","price-target-summary"):allowed={"symbol"}
        else:allowed={"symbol","limit"}
        if set(params)!=allowed:raise ValidationError("FMP endpoint parameters differ from their selected contract")
        if "period" in params and params["period"] not in ("annual","quarter"):
            raise ValidationError("FMP selected period is invalid")
        if "limit" in params:
            maximum=100 if unit.endpoint in ("analyst-estimates","revenue-product-segmentation") else 1000
            if not 1<=int(params["limit"])<=maximum:raise ResourceLimitError("FMP endpoint row limit exceeds its bound")
        if unit.endpoint in ("dividends","splits") and params["limit"]!="1000":
            raise ValidationError("FMP action request limit differs")
        if "page" in params and not 0<=int(params["page"])<=9:raise ResourceLimitError("FMP estimate page exceeds its bound")
        path="/api/v4/price-target" if unit.endpoint=="price-target" else "/stable/"+unit.endpoint
        return RequestRoute("financialmodelingprep.com",path,unit.parameters,("FMP_API_KEY",),"apikey")
    if unit.provider=="sec":
        cik=unit.subject
        if unit.endpoint=="submissions":path="/submissions/CIK"+cik+".json"
        elif unit.endpoint=="companyfacts":path="/api/xbrl/companyfacts/CIK"+cik+".json"
        elif unit.endpoint=="submissions-history":path="/submissions/"+params["file"]
        else:raise ValidationError("SEC endpoint has no selected host route")
        expected={"cik","file"} if unit.endpoint=="submissions-history" else {"cik"}
        if set(params)!=expected:raise ValidationError("SEC route has unexpected query fields")
        return RequestRoute("data.sec.gov",path,(),("SEC_USER_AGENT_NAME","SEC_USER_AGENT_EMAIL"),None)
    if unit.provider=="sharadar":
        if unit.endpoint in ("SHARADAR_DIRECT/fundamentals","SHARADAR_DIRECT/descriptions"):
            return RequestRoute("api.sharadar.com","/v1.0/data/"+unit.endpoint.split("/")[1],
                unit.parameters,("SHARADAR_DIRECT_API",),None,"x-api-key")
        if unit.endpoint not in ("SHARADAR/SF1","SHARADAR/SF1/metadata","SHARADAR/INDICATORS","SHARADAR/INDICATORS/metadata"):
            raise ValidationError("Sharadar route is outside the SF1 contract")
        return RequestRoute("data.nasdaq.com","/api/v3/datatables/"+unit.endpoint+".json",
            unit.parameters,("SHARADAR_API_KEY",),"api_key")
    raise ValidationError("This provider retains its independent host transport")

class ProviderRequestFailure(StoreUnavailableError):
    """Credential-free outcome from a terminated HTTP worker."""
    CATEGORIES=frozenset(('network_error','timeout','response_too_large','invalid_response',
        'unexpected_redirect','unexpected_content_type','credential_exposure','worker_lost'))
    def __init__(self,category,http_status=None,*,retryable=False):
        if category not in self.CATEGORIES or (http_status is not None and
                (type(http_status) is not int or not 100<=http_status<=599)) or type(retryable) is not bool:
            raise ValidationError('Provider failure diagnostic is invalid')
        self.category=category;self.http_status=http_status;self.retryable=retryable
        super().__init__('Provider request failed: '+category)
    def diagnostic(self):
        return {'contract':'quant_data.provider_failure.v1','category':self.category,
            'http_status':self.http_status,'retryable':self.retryable}


def _http_once(sender,route,credential,timeout_seconds,max_bytes,diagnostics=False):
    connection=None;status=None;category='network_error'
    try:
        parameters=dict(route.parameters)
        headers={"Accept":"application/json","Accept-Encoding":"identity","User-Agent":"QuantDataInfra/1.0"}
        if route.credential_header:headers[route.credential_header]=credential
        elif route.credential_parameter is None:headers["User-Agent"]=credential
        else:parameters[route.credential_parameter]=credential
        path=route.path+("?" + urlencode(parameters) if parameters else "")
        connection=http.client.HTTPSConnection(route.host,timeout=timeout_seconds)
        connection.request("GET",path,headers=headers)
        response=connection.getresponse();status=response.status
        if 300<=response.status<400:
            category='unexpected_redirect';raise ValueError()
        declared=response.getheader("Content-Length")
        if declared is not None and not declared.isdigit():
            category='invalid_response';raise ValueError()
        if declared is not None and int(declared)>max_bytes:
            category='response_too_large';raise ValueError()
        body=response.read(max_bytes+1)
        if declared is not None and len(body)!=int(declared):
            category='invalid_response';raise ValueError()
        if len(body)>max_bytes:
            category='response_too_large';raise ValueError()
        if credential.encode() in body or quote(credential,safe="").encode() in body:
            category='credential_exposure';raise ValueError()
        returned=tuple((k.lower(),v) for k,v in response.getheaders() if k.lower() in SAFE_HEADERS)
        if any(credential in v or quote(credential,safe="") in v for _,v in returned):
            category='credential_exposure';raise ValueError()
        media=(response.getheader("Content-Type") or "").split(";",1)[0].strip().lower()
        if response.status==200 and media!="application/json" and not media.endswith("+json"):
            category='unexpected_content_type';raise ValueError()
        sender.send((int(response.status),body,datetime.now(timezone.utc).isoformat(),returned))
    except Exception as error:
        if isinstance(error,(TimeoutError,socket.timeout)):category='timeout'
        failure=ProviderRequestFailure(category,status,retryable=category in ('network_error','timeout','invalid_response'))
        try:sender.send(failure.diagnostic() if diagnostics else None)
        except Exception:pass
    finally:
        if connection is not None:connection.close()
        sender.close()

def _receive_until(receiver,deadline,max_bytes):
    # Pipe readability only promises part of a frame. Keep every read inside
    # the original request deadline; the child may stall while sending a body.
    fd=receiver.fileno();os.set_blocking(fd,False)
    def exact(count):
        chunks=[];remaining=count
        while remaining:
            seconds=deadline-time.monotonic()
            if seconds<=0 or not select.select((fd,),(),(),max(0,seconds))[0]:
                raise ResourceLimitError("Selected provider request exceeded wall timeout")
            try:chunk=os.read(fd,min(remaining,65536))
            except BlockingIOError:continue
            if not chunk:raise EOFError()
            chunks.append(chunk);remaining-=len(chunk)
        return b"".join(chunks)
    length=struct.unpack("!i",exact(4))[0]
    if not 0<=length<=max_bytes+1024*1024:
        raise StoreUnavailableError("Selected provider response frame exceeds its bound")
    # Only our own forked worker writes this channel.
    payload=exact(length)
    if time.monotonic()>=deadline:
        raise ResourceLimitError("Selected provider request exceeded wall timeout")
    return pickle.loads(payload)

class SelectedProviderFetch:
    """Host seam: supplied credentials are never part of a request identity."""
    def __init__(self,provider,credential,*,credential_name=None):
        if provider not in ("fmp","sec","sharadar"):raise ValidationError("Selected host provider is unsupported")
        self.provider=provider
        self.credential_name=credential_name or {"fmp":"FMP_API_KEY","sec":"SEC_USER_AGENT_NAME","sharadar":"SHARADAR_API_KEY"}[provider]
        self._credential=_validate_value(credential)
    @property
    def secret_values(self):return (self._credential,)
    def __call__(self,*,unit,timeout_seconds,max_bytes):
        route=request_route(unit)
        if (self.credential_name not in route.credential_names or unit.provider!=self.provider or type(timeout_seconds) is not int or not 1<=timeout_seconds<=unit.timeout_seconds
            or type(max_bytes) is not int or not 1<=max_bytes<=unit.max_response_bytes):
            raise ValidationError("Selected transport differs from its exact request bound")
        def request_with_timeout(remaining):
            context=multiprocessing.get_context("fork")
            receiver,sender=context.Pipe(duplex=False)
            child=context.Process(target=_http_once,args=(sender,route,self._credential,remaining,max_bytes))
            deadline=time.monotonic()+remaining
            child.start();sender.close()
            try:
                result=_receive_until(receiver,deadline,max_bytes)
                if not isinstance(result,tuple) or len(result)!=4:raise StoreUnavailableError("Selected provider request failed")
                status,body,captured,headers=result
                return QueueResponse(status,body,captured,headers)
            except (EOFError,OSError):
                raise StoreUnavailableError("Selected provider request failed") from None
            finally:
                receiver.close()
                if child.is_alive():child.terminate()
                child.join(timeout=1)
                if child.is_alive():child.kill();child.join()
        if self.provider!="fmp":return request_with_timeout(timeout_seconds)
        from .collection_provider_policy import invoke_host_fmp
        return invoke_host_fmp(request_with_timeout,request_material=unit.request_material(),
            priority="backfill" if unit.mode=="historical_backfill" else "maintenance",
            timeout_seconds=timeout_seconds)

def host_fetch(project_root,provider,*,environment=None):
    # Invoke only after the host has checked an active binding and finite manifest.
    names={"fmp":("FMP_API_KEY",),"sec":("SEC_USER_AGENT_NAME","SEC_USER_AGENT_EMAIL"),"sharadar":("SHARADAR_DIRECT_API",)}
    if provider not in names:raise ValidationError("Selected host provider is unsupported")
    values=[read_project_credential(project_root=project_root,name=name,
        environment=os.environ if environment is None else environment) for name in names[provider]]
    if provider=="sec":
        from .sec_market_companyfacts_refresh import _credential_component
        values=[_credential_component(values[0],kind="name"),_credential_component(values[1],kind="email")]
    return SelectedProviderFetch(provider," ".join(values),credential_name=names[provider][0])
