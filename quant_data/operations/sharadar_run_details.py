"""Closed Sharadar run details; no provider payloads, credentials or paths."""
from contextvars import ContextVar

_CAPTURE_DETAILS = ContextVar("sharadar_run_details", default=None)
BATCH = "quant-data-sharadar-selected-refresh.timer"
FIELDS = {"outcome","http_status","dimension","symbol_count","completed_partitions",
          "total_partitions","requests","retries_today","next_retry_at","recovery"}


def validate_details(value):
    from datetime import datetime
    if not isinstance(value,dict) or set(value)!=FIELDS:
        raise ValueError("Invalid Sharadar details")
    if value["outcome"] not in {"succeeded","already_complete","provider_http_failure",
            "metadata_http_failure","page_http_failure","retry_next_day","invocation_budget",
            "daily_budget","complete_with_gaps","acquired_with_gaps","retained_evidence_required"}:
        raise ValueError("Invalid Sharadar outcome")
    if value["recovery"] not in {"next_day","budget","complete","blocked"}:
        raise ValueError("Invalid Sharadar recovery")
    if value["dimension"] not in {None,"ARQ","ART","ARY","MRQ","MRT","MRY"}:
        raise ValueError("Invalid Sharadar dimension")
    status=value["http_status"]
    if status is not None and (type(status) is not int or not 400<=status<=599):
        raise ValueError("Invalid Sharadar HTTP status")
    for key,cap in (("symbol_count",30),("completed_partitions",450),
            ("total_partitions",450),("requests",500),("retries_today",2)):
        if type(value[key]) is not int or not 0<=value[key]<=cap:
            raise ValueError("Invalid Sharadar count")
    if value["next_retry_at"] is not None:
        stamp=datetime.fromisoformat(value["next_retry_at"].replace("Z","+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("Invalid Sharadar retry time")
    return dict(value)


def capture_details(value):
    capture=_CAPTURE_DETAILS.get()
    if capture is not None:
        try:
            capture[:]=[validate_details(value)]
        except (ValueError, TypeError, AttributeError):
            return
