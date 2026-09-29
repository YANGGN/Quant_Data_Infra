"""Optional bounded browser Web Push with strict known-service endpoint validation."""
from __future__ import annotations
from urllib.parse import urlsplit
import ipaddress,re,socket
from .model import canonical

KEY=re.compile(r"[A-Za-z0-9_-]+={0,2}\Z")
ALLOWED=("fcm.googleapis.com","updates.push.services.mozilla.com","web.push.apple.com")
SUFFIXES=(".push.services.mozilla.com",".notify.windows.com")

def validate_subscription(value):
    if not isinstance(value,dict) or not isinstance(value.get("id"),str) or not 1<=len(value["id"])<=160:
        raise ValueError("push_subscription_identity")
    endpoint=value.get("endpoint")
    if not isinstance(endpoint,str) or len(endpoint)>2048:raise ValueError("push_endpoint")
    url=urlsplit(endpoint)
    host=url.hostname or ""
    if url.scheme!="https" or not host or host not in ALLOWED and not any(host.endswith(s) for s in SUFFIXES):
        raise ValueError("push_endpoint_allowlist")
    if url.username or url.password or url.port or url.fragment:raise ValueError("push_endpoint_authority")
    keys=value.get("keys")
    if not isinstance(keys,dict) or set(keys)!={"p256dh","auth"}:raise ValueError("push_keys")
    if any(not isinstance(keys[k],str) or not KEY.fullmatch(keys[k]) or len(keys[k])>180 for k in keys):
        raise ValueError("push_keys")
    disabled=value.get("disabled_symbols",[])
    if not isinstance(disabled,list) or len(disabled)>25 or any(not isinstance(s,str) or not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,11}",s) for s in disabled):
        raise ValueError("push_disabled_symbols")
    return {"endpoint":endpoint,"keys":keys}

def verify_public_resolution(endpoint):
    host=urlsplit(endpoint).hostname
    addresses=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
    if not addresses:raise ValueError("push_dns_empty")
    for entry in addresses:
        address=ipaddress.ip_address(entry[4][0])
        if not address.is_global:raise ValueError("push_private_address")

def send_push(subscription,alert,private_key):
    """One attempt; dependency installation and VAPID provisioning are integration steps."""
    verified=validate_subscription(subscription)
    verify_public_resolution(verified["endpoint"])
    import requests
    from pywebpush import webpush
    class NoRedirectSession(requests.Session):
        def post(self,url,**kwargs):
            kwargs["allow_redirects"]=False
            return super().post(url,**kwargs)
    data=canonical({"id":alert["id"],"title":alert["title"],"body":alert["reason"]})
    with NoRedirectSession() as session:
        session.trust_env=False
        response=webpush(subscription_info=verified,data=data,
            vapid_private_key=private_key,
            vapid_claims={"sub":"https://quant-options-monitor.yanggainan.chatgpt.site"},
            timeout=8,ttl=1800,requests_session=session)
        if not 200<=response.status_code<=202:raise RuntimeError("push_delivery_failed")
    return True
