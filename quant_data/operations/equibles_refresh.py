"""Fixed daily Equibles incremental refresh; account-wide quota and retained pages."""
from __future__ import annotations
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median
import hashlib
import json
import sys
import time

from ..company.equibles_queue_policy import CONTRACT, DAILY_CAP, TZ, lane_quotas, next_task as select_next_task
from . import equibles_transcript_backfill as old
from .equibles_paid_policy import validate_checkpoint, quota_bucket, apply_paid_headers, operating_daily_cap
from ..company.equibles_transcripts import event_page, transcript_page, validate_bundle, RawPage, MAX_PAGES
from ..errors import ConflictError, ValidationError, ResourceLimitError
from ..ingestion import PublicationDeferred
from ..stores import acquire_write_session, quiet_immutable_read_connection
from ..json_codec import dumps_strict

ROOT = old.PROJECT_ROOT
STATE = "data/.operations/equibles-refresh"
TIMER = "quant-data-equibles-refresh.timer"
MAX_SECONDS = 3600
MAX_BYTES = 400 * 1024 * 1024
RECHECK_DAYS = (1, 3, 7, 14)
RETRYABLE_HTTP = {408, 500, 502, 503, 504}


def load(path):
    return json.loads(old.read_file(path, 32 * 1024 * 1024))


def stamp(value):
    return old.utc(value.isoformat())


def day_of(value):
    try:
        return date.fromisoformat(value[:10]) if isinstance(value, str) else None
    except ValueError:
        return None


def call_key(symbol, event):
    return f"{symbol}:{event['fiscalYear']}:{event['fiscalQuarter']}"


def read_inventory(stores, roster, now):
    """Local immutable selection: latest FMP capture membership, exact instrument mapping."""
    ids = {r["instrument_id"]: r["symbol"] for r in roster}
    output = {r["symbol"]: dict(instrument_id=r["instrument_id"], earnings_dates=[],
              calendar_captured_at=None, call_dates=[], known={}, latest_capture=None) for r in roster}
    with acquire_write_session(stores, ("company",), timeout_seconds=300):
        with quiet_immutable_read_connection(stores, "company") as c:
            captures = list(c.execute("""SELECT capture_id,instrument_id,captured_at FROM (
                SELECT capture_id,instrument_id,captured_at,
                row_number() OVER(PARTITION BY instrument_id ORDER BY captured_at DESC,capture_id DESC) n
                FROM company_fmp_analyst_captures WHERE endpoint='earnings' AND captured_at<=?)
                WHERE n=1""", (stamp(now),)))
            cutoff = (now.astimezone(TZ).date()-timedelta(days=14)).isoformat()
            for row in captures:
                symbol = ids.get(row["instrument_id"])
                if symbol is None:
                    continue
                item = output[symbol]
                item["calendar_captured_at"] = row["captured_at"]
                item["earnings_dates"] = sorted({r[0] for r in c.execute("""
                    SELECT v.source_event_date FROM company_fmp_analyst_capture_membership m
                    JOIN company_fmp_analyst_observation_versions v ON v.observation_version_id=m.observation_version_id
                    WHERE m.capture_id=? AND v.source_event_date>=?""", (row["capture_id"], cutoff))})
            for row in c.execute("""SELECT symbol,instrument_id,event_id,fiscal_year,fiscal_quarter,
                    event_json,captured_at,capture_id FROM company_equibles_transcripts WHERE captured_at<=?""", (stamp(now),)):
                symbol = ids.get(row["instrument_id"])
                if symbol is None or symbol != row["symbol"]:
                    continue
                item = output[symbol]
                event = json.loads(row["event_json"])
                key = f"{symbol}:{row['fiscal_year']}:{row['fiscal_quarter']}"
                item["known"][key] = row["capture_id"]
                call_date = day_of(event.get("callDate"))
                if call_date:
                    item["call_dates"].append(call_date.isoformat())
                if item["latest_capture"] is None or row["captured_at"] > item["latest_capture"]:
                    item["latest_capture"] = row["captured_at"]
    for item in output.values():
        item["call_dates"] = sorted(set(item["call_dates"]))[-5:]
    return output


def classify(info, watch, today):
    """Calendar dates and historical estimates only prioritize checks; never label facts."""
    dates = sorted(d for raw in info["earnings_dates"] if (d := day_of(raw)) is not None)
    past = [d for d in dates if today-timedelta(days=14) <= d < today]
    latest_call = max((day_of(d) for d in info["call_dates"]), default=None)
    if past and (latest_call is None or max(past) > latest_call):
        report_day = max(past)
        attempt = watch.get("report_day") == report_day.isoformat()
        if attempt:
            due = day_of(watch.get("next_due"))
            if due and due > today:
                return None
        return ("delayed" if attempt else "recent", report_day.isoformat())
    # Unresolved expected releases persist beyond the initial calendar window.
    if watch.get("awaiting") and (day_of(watch.get("next_due", "")) or today) <= today:
        return "delayed", watch.get("report_day")
    history = [day_of(d) for d in info["call_dates"] if day_of(d)]
    intervals = [(b-a).days for a,b in zip(history, history[1:]) if 45 <= (b-a).days <= 400]
    expected = history[-1]+timedelta(days=int(median(intervals))) if len(intervals)>=2 else None
    last = day_of(watch.get("last_checked") or info.get("latest_capture"))
    if last and last >= today:
        return None
    gap = (today-last).days if last else 100000
    observed = day_of(info.get("calendar_captured_at"))
    calendar_stale = observed is None or (today-observed).days > 14
    missing_forward = not any(d >= today for d in dates)
    if expected and expected-timedelta(days=7) <= today and gap >= 7:
        return "fallback", expected.isoformat()
    if (calendar_stale or missing_forward or not history) and gap >= 30:
        return "fallback", None
    return None


def sync_queue(state, inventory, today):
    for symbol, info in inventory.items():
        watch = state["watch"].setdefault(symbol, {})
        # Published captures can resolve a prior expected report without another GET.
        if watch.get("report_day") and any(d >= watch["report_day"] for d in info["call_dates"]):
            watch["awaiting"] = False
        if symbol in state["tasks"]:
            continue
        choice = classify(info, watch, today)
        if choice:
            lane, report = choice
            state["tasks"][symbol] = dict(symbol=symbol, instrument_id=info["instrument_id"],
                lane=lane, report_day=report, queued_at=today.isoformat(), due=today.isoformat(),
                phase="catalogue", events=[], pages=[], offset=0, failures=0,
                catalogue_round=0, blocked=False)


def next_task(state, today, spent, quotas):
    return select_next_task(state, today, spent, quotas, parse_day=day_of)


def finish_check(state, task, today, awaiting=False):
    watch = state["watch"].setdefault(task["symbol"], {})
    watch.update(last_checked=today.isoformat(), report_day=task["report_day"], awaiting=awaiting)
    origin = day_of(task["report_day"]) or today
    future = [origin+timedelta(days=n) for n in RECHECK_DAYS if origin+timedelta(days=n)>today]
    watch["next_due"] = (future[0] if future else today+timedelta(days=7)).isoformat()
    if awaiting:
        task.update(phase="catalogue", events=[], pages=[], offset=0, failures=0, lane="delayed",
                    due=watch["next_due"], catalogue_round=task["catalogue_round"]+1)
    else:
        del state["tasks"][task["symbol"]]


def initial_state(roster, first_slot):
    return dict(contract=CONTRACT, roster=roster, first_slot=first_slot, watch={}, tasks={},
                pending=None, daily={}, analysis_queue={}, last_finished_slot=None)


def validate_state(state, account):
    if state["contract"] != CONTRACT or state["roster"] != account["roster"]:
        raise ConflictError("Equibles refresh frozen roster changed")
    if account.get("operating_daily_cap") != DAILY_CAP:
        raise ConflictError("Equibles refresh requires its shared 100-call allocation")
    if account["pending"] or account["blocked"] or account["index"] != len(account["roster"]):
        raise ConflictError("Equibles historical work is not settled")


def settle_headers(account, pending, receipt):
    if receipt["captured_at"] < pending["at"]:
        raise ConflictError("Equibles response predates reservation")
    day = pending["at"][:10]
    headers = receipt["headers"]
    keys = ("x-ratelimit-limit","x-ratelimit-remaining","x-ratelimit-reset")
    if not any(k in headers for k in keys) and receipt["status"] != 200:
        if receipt["status"] == 429:
            quota_bucket(account, day)["remaining"] = 0
        return
    try:
        limit, remaining, reset = (int(headers[k]) for k in keys)
        if limit < 1 or not 0 <= remaining <= limit:
            raise ValueError()
        receipt_day = receipt["captured_at"][:10]
        valid = {int(datetime.combine(date.fromisoformat(d)+timedelta(days=1),
                 datetime.min.time(), timezone.utc).timestamp()):d for d in (day, receipt_day)}
        if reset not in valid:
            raise ValueError()
    except (KeyError,ValueError) as exc:
        raise ValidationError("Equibles refresh quota headers invalid") from exc
    bucket = quota_bucket(account, valid[reset])
    apply_paid_headers(account, bucket, limit=limit, remaining=remaining, reset=reset,
                       receipt_at=receipt["captured_at"], reservation_at=pending["at"])
    if receipt["status"] == 429:
        bucket["remaining"] = 0


def run_refresh(root, account_root, publisher, transport, inventory, *, clock=old.now,
                monotonic=time.monotonic, sleeper=time.sleep):
    """Explicit temporary-root harness; production entry point has no arguments."""
    started = monotonic()
    now = clock()
    today = now.astimezone(TZ).date()
    utc_day = now.astimezone(timezone.utc).date().isoformat()
    deadline = started+MAX_SECONDS
    with old.job_lock(root), old.job_lock(account_root):
        account = load(account_root/"state.json")
        validate_checkpoint(account)
        state = load(root/"state.json")
        validate_state(state, account)
        for name in ("runs",):
            old.private_directory(root/name)
        day_state = state["daily"].setdefault(utc_day, dict(recent=0, delayed=0, fallback=0, bytes=0))
        spent = day_state
        quotas = lane_quotas(today)
        counts = Counter()
        def save():
            old.atomic(root/"state.json", state, replace=True)
        def save_account():
            old.atomic(account_root/"state.json", account, replace=True)
        sync_queue(state, inventory, today)
        save()

        def hold(task, reason, response_id=None):
            task.update(blocked=True, reason=reason)
            if response_id:
                task["rejected_response_id"] = response_id
            counts["failed"] += 1
            save()

        def process(pending, receipt, body):
            task = state["tasks"][pending["symbol"]]
            status = receipt["status"]
            spent["bytes"] += len(body)
            settle_headers(account, pending, receipt)
            save_account()
            state["pending"] = None
            if status in (401,403):
                task["blocked"] = True
                save()
                raise ConflictError("Equibles authentication failed")
            if status == 429:
                task["due"] = (today+timedelta(days=1)).isoformat()
                save()
                return "quota"
            if status in RETRYABLE_HTTP:
                task["failures"] += 1
                task["blocked"] = task["failures"] >= 2
                task["due"] = (today+timedelta(days=1)).isoformat()
                counts["failed"] += 1
                save()
                return
            if status not in (200,404):
                hold(task, "unexpected_http", pending["response_id"])
                return
            try:
                process_content(task, status, body, pending)
            except (ValidationError, ConflictError, ResourceLimitError, UnicodeDecodeError) as exc:
                hold(task, "response_" + type(exc).__name__, pending["response_id"])

        def process_content(task, status, body, pending):
            if task["phase"] == "catalogue":
                if status == 404:
                    counts["unavailable"] += 1
                    finish_check(state, task, today)
                    save()
                    return
                events, more = event_page(body, 0)
                floor = day_of(state["first_slot"])-timedelta(days=14)
                observed_dates = [day_of(e.get("callDate")) for e in events]
                if more and (not all(observed_dates) or min(observed_dates) >= floor):
                    raise ConflictError("Equibles current catalogue window is truncated")
                # Current refresh only; never re-open the completed historical walk.
                eligible = [e for e in events if day_of(e.get("callDate")) and
                            floor <= day_of(e["callDate"]) <= today and
                            (not e["hasTranscript"] or
                             call_key(task["symbol"],e) not in inventory[task["symbol"]]["known"])]
                identities = [call_key(task["symbol"], e) for e in eligible if e["hasTranscript"]]
                if len(set(identities)) != len(identities):
                    raise ConflictError("Ambiguous Equibles fiscal identity")
                available = [e for e in eligible if e["hasTranscript"]]
                task["awaiting_event"] = any(not e["hasTranscript"] for e in eligible)
                task["events"] = sorted(available, key=lambda e:(e["callDate"],e["id"]))
                task["catalogue_has_more"] = more
                if task["events"]:
                    task.update(phase="download",offset=0,pages=[],failures=0)
                else:
                    report = day_of(task["report_day"])
                    waiting = task["awaiting_event"] or bool(report and report < today)
                    finish_check(state,task,today,waiting)
                counts["checked"] += 1
            else:
                event = task["events"][0]
                if status == 404 or old.empty_transcript_page(body,symbol=task["symbol"],event=event,offset=task["offset"]):
                    task.update(due=(today+timedelta(days=3)).isoformat(),lane="delayed",failures=0)
                    counts["unavailable"] += 1
                else:
                    page = transcript_page(body,symbol=task["symbol"],event=event,offset=task["offset"])
                    task["pages"].append(pending["response_id"])
                    task["offset"] += page["turnCount"]
                    task["failures"] = 0
                    if len(task["pages"]) > MAX_PAGES:
                        raise ValidationError("Equibles transcript page cap exceeded")
                    task["phase"] = "download" if page["hasMore"] else "publish"
            save()

        # A durable received page is processed without another request. Uncertain
        # interrupted attempts are held, charged, and never silently repeated.
        if state["pending"]:
            pending=state["pending"]
            found=old.response_receipt(account_root,pending["response_id"],pending["path"])
            if found:
                process(pending,*found)
            else:
                state["tasks"][pending["symbol"]]["blocked"]=True
                state["tasks"][pending["symbol"]]["reason"]="uncertain_request"
                state["pending"]=None
                counts["failed"]+=1
                save()
        outcome="complete"
        while monotonic() < deadline-35 and clock().astimezone(timezone.utc).date().isoformat()==utc_day:
            chosen=next_task(state,today,spent,quotas)
            if chosen is None:
                break
            symbol,lane=chosen
            task=state["tasks"][symbol]
            if task["phase"] == "publish":
                event=task["events"][0]
                pages=[]
                try:
                    for identifier in task["pages"]:
                        receipt=load(account_root/"responses"/(identifier+".json"))
                        found=old.response_receipt(account_root,identifier,receipt["path"])
                        r,raw=found
                        pages.append(RawPage(raw,r["captured_at"],"equibles-transcripts/blobs/"+r["sha256"]+".json",dumps_strict(r["headers"])))
                    validate_bundle(symbol,task["instrument_id"],event,tuple(pages))
                except (ValidationError, ConflictError, ResourceLimitError, UnicodeDecodeError) as exc:
                    hold(task, "bundle_" + type(exc).__name__)
                    continue
                try:
                    result=publisher.publish(symbol=symbol,instrument_id=task["instrument_id"],
                        event=event,pages=tuple(pages),deadline=deadline,monotonic=monotonic)
                except (ConflictError,PublicationDeferred):
                    outcome="publication_deferred"
                    break
                semantic=result.semantic_identity
                from ..stores import stable_id
                capture=stable_id("equibles_transcript",semantic)
                state["analysis_queue"].setdefault(capture,dict(symbol=symbol,status="pending",captured_at=max(p.captured_at for p in pages)))
                inventory[symbol]["known"][call_key(symbol,event)]=capture
                counts["saved" if result.outcome!="unchanged" else "unchanged"]+=1
                task["events"].pop(0)
                task.update(pages=[],offset=0,phase="download",failures=0)
                if not task["events"]:
                    finish_check(state,task,today,task.get("awaiting_event",False))
                save()
                continue
            if task["phase"] == "download" and len(task["pages"]) >= MAX_PAGES:
                hold(task, "transcript_page_cap")
                continue
            bucket=quota_bucket(account,utc_day)
            if bucket["attempted"]>=DAILY_CAP or bucket["remaining"]<=0:
                outcome="daily_cap"
                break
            if spent["bytes"]+old.MAX_BYTES>MAX_BYTES:
                outcome="byte_cap"
                break
            if task["phase"]=="catalogue":
                path=f"/v1/stocks/{symbol}/investor-events?eventType=EarningsCall&limit=100&offset=0"
            else:
                e=task["events"][0]
                path=f"/v1/stocks/{symbol}/earnings-calls/{e['fiscalYear']}/{e['fiscalQuarter']}/speakers?limit=200&offset={task['offset']}"
            previous=account.get("last_request_at")
            elapsed=(clock()-datetime.fromisoformat(previous.replace("Z","+00:00"))).total_seconds() if previous else 1
            if elapsed<0:
                raise ConflictError("Equibles refresh clock moved backwards")
            if elapsed<1:
                sleeper(1-elapsed)
            if clock().astimezone(timezone.utc).date().isoformat()!=utc_day or monotonic()>=deadline-35:
                outcome="deadline"
                break
            at=stamp(clock())
            number=bucket["attempted"]+1
            pending=dict(symbol=symbol,path=path,at=at,attempt=f"{utc_day}-{number:03d}",
                         response_id=old.request_id(f"refresh:{utc_day}:{number}:{path}"))
            bucket["attempted"]+=1
            bucket["remaining"]=max(0,bucket["remaining"]-1)
            account["last_request_at"]=at
            save_account()  # conservative charge is durable before every dispatch
            spent[lane]+=1
            state["pending"]=pending
            old.atomic(account_root/"attempts"/(pending["attempt"]+".json"),pending)
            save()
            counts["requests"]+=1
            try:
                status,headers,body=transport.request(path)
            except old.EquiblesTransportFailure as exc:
                old.atomic(root/"runs"/(pending["response_id"]+".failure.json"),exc.diagnostic())
                task["failures"]+=1
                task["blocked"]=task["failures"]>=2 or exc.category not in ("timeout","connection_error","incomplete_response","worker_lost")
                task["due"]=(today+timedelta(days=1)).isoformat()
                state["pending"]=None
                counts["failed"]+=1
                save()
                continue
            receipt=old.retain_response(account_root,path,body,stamp(clock()),headers,status,identifier=pending["response_id"])
            if process(pending,receipt,body)=="quota":
                outcome="provider_quota"
                break
        else:
            outcome="deadline"
        blocked=sum(bool(t.get("blocked")) for t in state["tasks"].values())
        result=dict(contract=CONTRACT,at=stamp(clock()),outcome=outcome,requests=counts["requests"],
            saved=counts["saved"],unchanged=counts["unchanged"],checked=counts["checked"],
            failed=counts["failed"],unavailable=counts["unavailable"],blocked=blocked,
            pending=len(state["tasks"]),mode="weekend_fallback" if today.weekday()>=5 else "weekday",
            daily_cap=DAILY_CAP,charged_today=quota_bucket(account,utc_day)["attempted"],
            lane_calls={k:spent[k] for k in quotas},queued_for_luna=len(state["analysis_queue"]),
            counts=dict(unit="steps",successful=counts["saved"]+counts["unchanged"]+counts["checked"],
                        failed=counts["failed"],partial=0,skipped=counts["unavailable"],unattempted=len(state["tasks"])))
        save_account()
        save()
        old.atomic(root/"status.json",result,replace=True)
        return result


def due_slot(now):
    local=now.astimezone(TZ)
    day=local.date() if local.hour>=2 else local.date()-timedelta(days=1)
    return datetime.combine(day,datetime.min.time(),TZ).replace(hour=2)


def run_live():
    root=ROOT/STATE
    activation=load(root/"activation.json")
    now=old.now()
    slot=due_slot(now)
    if slot<datetime.fromisoformat(activation["first_slot"]):
        return dict(outcome="not_due",requests=0)
    with old.job_lock(root/"schedule"):
        day=slot.date().isoformat()
        old.private_directory(root/"slots")
        receipt=root/"slots"/(day+".json")
        if receipt.exists():
            return load(receipt)
        account=load(old.STATE_ROOT/"state.json")
        state=load(root/"state.json")
        validate_state(state,account)
        inventory=read_inventory(old.stores(),state["roster"],now)
        registry=old.load_registry(old.CANONICAL_REGISTRY_PATH,project_root=ROOT,environment={})
        key=old.read_project_credential(project_root=ROOT,name="EQUIBLES_API_KEY",environment={})
        result=run_refresh(root,old.STATE_ROOT,old.EquiblesTranscriptPublisher(old.stores(),registry),
                           old.EquiblesTransport(key,start_method="spawn"),inventory)
        result["slot"]=slot.isoformat()
        old.atomic(receipt,result)
        return result


def main(argv=None):
    if tuple(sys.argv[1:] if argv is None else argv):
        return 64
    try:
        result=run_live()
        from .fetch_run_summary import record_report
        record_report(TIMER,result)
        print(dumps_strict(result))
        return 75 if result.get("failed") or result.get("blocked") or result["outcome"]=="publication_deferred" else 0
    except Exception as exc:
        print(dumps_strict(dict(outcome="failed",error_type=type(exc).__name__)))
        return 75


if __name__=="__main__":
    from .fetch_run_history import run_recorded_cli
    raise SystemExit(run_recorded_cli("quant-data-equibles-refresh.timer",main,argv=sys.argv[1:]))
