"""Fixed-origin collector channel with independent bearer credentials."""
from __future__ import annotations
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import build_opener,HTTPSHandler,HTTPRedirectHandler,Request
from urllib.error import HTTPError,URLError
import json,os,stat
from .model import canonical

ORIGIN="https://quant-options-monitor.yanggainan.chatgpt.site"
class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise ValueError("site_redirect_forbidden")

def connection(path:Path)->dict:
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_mode&0o077 or info.st_size>16384:
            raise ValueError("monitor_connection_permissions")
        data=os.read(fd,16385)
    finally:os.close(fd)
    value=json.loads(data)
    if value.get("site_url")!=ORIGIN or not all(isinstance(value.get(k),str) and len(value[k])>=32
        for k in ("dispatch_token","collector_token")):
        raise ValueError("monitor_connection_contract")
    return value

class SiteClient:
    def __init__(self,config:dict,*,opener=None):
        if config["site_url"]!=ORIGIN:raise ValueError("monitor_site_origin")
        self.config=config
        self.opener=opener or build_opener(HTTPSHandler(),NoRedirect())
    def request(self,path,payload=None):
        if path not in ("/api/collector/config","/api/collector/publish","/api/collector/daily"):
            raise ValueError("monitor_site_route")
        url=ORIGIN+path
        parsed=urlsplit(url)
        if parsed.scheme!="https" or parsed.netloc!="quant-options-monitor.yanggainan.chatgpt.site":
            raise ValueError("monitor_site_origin")
        body=None if payload is None else canonical(payload).encode()
        if body is not None and len(body)>900000:raise ValueError("monitor_publication_bytes")
        headers={"OAI-Sites-Authorization":"Bearer "+self.config["dispatch_token"],
                 "Authorization":"Bearer "+self.config["collector_token"],"Accept":"application/json"}
        if body is not None:headers["Content-Type"]="application/json"
        req=Request(url,data=body,headers=headers,method="GET" if body is None else "POST")
        try:
            with self.opener.open(req,timeout=15) as response:
                if response.status!=200 or response.url!=url:raise ValueError("monitor_site_response")
                raw=response.read(1048577)
                if len(raw)>1048576:raise ValueError("monitor_site_response_cap")
                return json.loads(raw)
        except (HTTPError,URLError) as exc:
            raise RuntimeError("monitor_site_unavailable") from None
    def config_get(self):return self.request("/api/collector/config")
    def publish(self,value):
        result=self.request("/api/collector/publish",value)
        if result.get("ok") is not True:raise ValueError("monitor_site_publication_rejected")
        return result

    def daily_state(self):return self.request("/api/collector/daily")
    def daily_publish(self,value):
        result=self.request("/api/collector/daily",value)
        if result.get("ok") is not True:raise ValueError("daily_publication_rejected")
        return result
