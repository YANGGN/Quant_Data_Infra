"""Observational Equibles saved-queue projection; never invokes a collector."""
from dataclasses import replace
from datetime import datetime,date,time,timedelta,timezone
from pathlib import Path
import copy
import re
from zoneinfo import ZoneInfo
from quant_data.contracts import TruncationV1
from quant_data.errors import StoreUnavailableError,ValidationError,ResourceLimitError,ConflictError
from quant_data.json_codec import loads_strict
from quant_data.operations.equibles_transcript_backfill import read_file
from .retained_research_common import instant,result,digest

TZ=ZoneInfo("America/Toronto")
MAX_BYTES=32*1024*1024
# Saved queue v1 policy; observational projection does not import a live collector.
CONTRACT="quant_data.equibles_incremental.v1"
DAILY_CAP=100

def _next_saved_task(state, today, spent, quotas):
    weekend = today.weekday() >= 5
    candidates = []
    for symbol, task in state["tasks"].items():
        if task.get("blocked") or date.fromisoformat(task["due"]) > today:
            continue
        partial = task["phase"] in ("download", "publish")
        lane = "fallback" if weekend else task["lane"]
        if weekend and task["lane"] != "fallback" and not partial and task["lane"] != "delayed":
            continue
        priority = (0 if partial else 1, state["watch"].get(symbol, {}).get("last_checked", ""),
                    task["queued_at"], symbol)
        candidates.append((lane, priority, symbol))
    if not candidates:
        return None
    protected = [x for x in candidates if spent[x[0]] < quotas[x[0]]]
    partials = [x for x in candidates if x[1][0] == 0]
    choices = partials or protected or candidates  # complete retained work before discovery
    lane, _, symbol = min(choices, key=lambda x:x[1])
    return symbol, lane

def saved(root,relative,bound):
    path=root/relative
    # Only fixed host paths. Reject directory aliases as well as unsafe final files.
    for parent in [path.parent,*path.parent.parents]:
        if parent==root:break
        if parent.is_symlink():raise StoreUnavailableError("Saved collector state has an invalid directory")
    before=path.lstat()
    body=read_file(path,bound)
    after=path.lstat()
    if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):
        raise StoreUnavailableError("Saved collector state changed during the read")
    return loads_strict(body,max_bytes=bound),digest(body.decode()),after

def next_slot(now):
    local=now.astimezone(TZ)
    for offset in range(3):
        value=datetime.combine(local.date()+timedelta(days=offset),time(2),TZ)
        # Match wall-clock schedule without inventing a nonexistent DST time.
        if value.astimezone(timezone.utc).astimezone(TZ)!=value:continue
        if value>now:return value.isoformat()
    raise ValidationError("Next schedule slot could not be represented")

def project(state,account,today,now,limit,symbols=()):
    if state.get("contract")!=CONTRACT or not isinstance(state.get("tasks"),dict) or len(state["tasks"])>5000:
        raise StoreUnavailableError("Saved Equibles queue is invalid")
    if state.get("roster")!=account.get("roster"):raise StoreUnavailableError("Saved queue and account membership differ")
    tasks=state["tasks"];watch=state.get("watch",{})
    if not isinstance(watch,dict):raise StoreUnavailableError("Saved Equibles watch state is invalid")
    utc_day=now.astimezone(timezone.utc).date().isoformat()
    usage=account.get("usage",{}).get(utc_day,{})
    charged=usage.get("attempted")
    if charged is not None and (type(charged) is not int or charged<0):raise StoreUnavailableError("Saved quota is invalid")
    cap=account.get("operating_daily_cap",DAILY_CAP)
    if type(cap) is not int or not 1<=cap<=DAILY_CAP:raise StoreUnavailableError("Saved account cap differs from this collector")
    recorded_remaining=usage.get("remaining",cap)
    if type(recorded_remaining) is not int or recorded_remaining<0:
        raise StoreUnavailableError("Saved remaining quota is invalid")
    remaining=min(cap-charged,recorded_remaining) if charged is not None else None
    spent=state.get("daily",{}).get(utc_day,{})
    quotas=dict(recent=80,delayed=15,fallback=5) if today.weekday()<5 else dict(recent=0,delayed=0,fallback=100)
    work=dict(tasks=copy.deepcopy(tasks),watch=copy.deepcopy(watch))
    ordered=[]
    for symbol,task in work["tasks"].items():
        if (not isinstance(symbol,str) or not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-^]{0,19}",symbol)
            or not isinstance(task,dict) or task.get("symbol")!=symbol or task.get("lane") not in quotas
            or task.get("phase") not in ("catalogue","download","publish") or type(task.get("blocked")) is not bool):
            raise StoreUnavailableError("Saved queue task is invalid")
        try:date.fromisoformat(task["due"])
        except (TypeError,ValueError,KeyError) as exc:raise StoreUnavailableError("Saved queue due date is invalid") from exc
    simulated={k:spent.get(k,0) for k in quotas}
    while work["tasks"]:
        selected=_next_saved_task(work,today,simulated,quotas)
        if selected is None:break
        symbol,lane=selected
        ordered.append(symbol);simulated[lane]+=1;del work["tasks"][symbol]
    selected_symbols=set(symbols) if symbols else set(tasks)
    output=[]
    order={s:i+1 for i,s in enumerate(ordered)}
    for symbol,task in sorted(tasks.items(),key=lambda kv:(order.get(kv[0],10000),kv[1]["due"],kv[0])):
        if symbol not in selected_symbols:continue
        eligible=symbol in order
        reason="blocked" if task["blocked"] else "backoff_until_due_date" if task["due"]>today.isoformat() else "saved_lane_budget_or_weekend_policy" if not eligible else "saved_queue_eligible"
        output.append(dict(symbol=symbol,instrument_id=task.get("instrument_id"),lane=task["lane"],
            phase=task["phase"],due_date=task["due"],report_day=task.get("report_day"),
            last_checked=watch.get(symbol,{}).get("last_checked"),eligible=eligible,reason=reason,
            queue_order_hint=order.get(symbol),blocked=task["blocked"],
            quota_status="exhausted" if remaining is not None and remaining<=0 else "unknown" if remaining is None else "remaining",
            attempts_may_require_multiple_requests=True))
    return output[:limit],dict(planning_date=today.isoformat(),queue_count=len(tasks),selected_queue_count=len(output),
        eligible_selected_count=sum(r["eligible"] for r in output),saved_daily_cap=cap,charged_requests=charged,
        remaining_request_budget=max(0,remaining) if remaining is not None else None,quota_utc_day=utc_day,
        lane_request_budgets=quotas,next_scheduled_slot=next_slot(now),
        schedule_basis="configured_daily_02_Toronto_not_live_timer_inspection",
        account_pending=bool(account.get("pending")),account_blocked=bool(account.get("blocked")),
        selection_basis="persisted_queue_only_calendar_reclassification_occurs_at_normal_run",
        order_basis="one_request_per_task_hint_not_execution_guarantee"),len(output)>limit

def invoke(args,context,registry):
    now=datetime.fromisoformat(instant(context.clock.instant()).replace("Z","+00:00"))
    today=date.fromisoformat(args["date"]) if args.get("date") else now.astimezone(TZ).date()
    if not 0<=(today-now.astimezone(TZ).date()).days<=7:raise ValidationError("Plan date must be today or within the next seven days")
    context.checkpoint()
    try:
        state,sha,stamp=saved(registry.project_root,"data/.operations/equibles-refresh/state.json",MAX_BYTES)
        account,account_sha,account_stamp=saved(registry.project_root,"data/.operations/equibles-transcripts/state.json",MAX_BYTES)
        tasks,metadata,more=project(state,account,today,now,args.get("limit",100),args.get("symbols",()))
        metadata.update(queue_state_digest=sha,account_state_digest=account_sha,
            state_file_modified_at=datetime.fromtimestamp(stamp.st_mtime,timezone.utc).isoformat(),
            provider_requests=0,collector="equibles_transcripts")
        path=registry.project_root/"data/.operations/equibles-refresh/slots"/(today.isoformat()+".json")
        if path.exists():
            receipt,_,_=saved(registry.project_root,"data/.operations/equibles-refresh/slots/"+today.isoformat()+".json",65536)
            metadata["saved_slot_receipt"]={k:receipt.get(k) for k in ("slot","at","outcome","requests","saved","checked","failed","daily_cap","charged_today")}
        for relative,original in (("data/.operations/equibles-refresh/state.json",stamp),
                                   ("data/.operations/equibles-transcripts/state.json",account_stamp)):
            current=(registry.project_root/relative).lstat()
            key=lambda x:(x.st_dev,x.st_ino,x.st_size,x.st_mtime_ns)
            if key(current)!=key(original):raise StoreUnavailableError("Saved queue changed across the state reads")
    except (OSError,KeyError,TypeError,ValueError,ConflictError) as exc:
        raise StoreUnavailableError("Saved Equibles collection plan is unavailable or invalid") from exc
    context.checkpoint()
    value=result("data.get_collection_plan",args,instant(now.isoformat()),
        [("collection_plan",metadata),*[("collection_plan_ticker",t) for t in tasks]],
        warnings=(("queue_projection","This reads saved queue and quota evidence. It does not reserve requests, query the provider, verify timer activation, or guarantee the next run's selection."),))
    return replace(value,truncation=TruncationV1(more,args.get("limit",100),len(tasks),metadata["selected_queue_count"],more))
