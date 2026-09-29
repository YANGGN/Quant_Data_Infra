"""Explicit paid transcript catalogue epochs and finite continuation decisions.

Epoch creation is offline. It preserves original checkpoints and quota charges;
only a separately authorized invocation can acquire the dated catalogue units.
"""
import copy
import hashlib
import re
from datetime import datetime, timedelta, timezone
from ..errors import ConflictError, ResourceLimitError, ValidationError
from ..json_codec import loads_strict, dumps_strict
from ..market.collection_universe import _utc
from .equibles_paid_policy import validate_checkpoint, POLICY


def catalogue_identifier(state, path):
    from .equibles_transcript_backfill import request_id
    epoch = state.get("catalogue_epoch")
    if epoch is not None and "/investor-events?" in path:
        return request_id("catalogue-epoch:" + epoch["id"] + ":" + path)
    return request_id(path)


def call_key(symbol, event):
    return symbol + ":" + str(event["fiscalYear"]) + ":" + str(event["fiscalQuarter"])


def _known_calls(root, state, *, cutoff):
    from .equibles_transcript_backfill import retained, page_from
    from ..company.equibles_transcripts import event_page, transcript_page, validate_bundle, MAX_EVENTS, MAX_PAGES
    epoch = state.get("catalogue_epoch")
    if epoch is not None:
        return copy.deepcopy(epoch["known_calls"])
    known = {}
    for symbol, completed in sorted(state["completed"].items()):
        rows = []
        for page in range(11):
            path = f"/v1/stocks/{symbol}/investor-events?eventType=EarningsCall&limit=100&offset={len(rows)}"
            found = retained(root, path)
            if found is None:
                raise ConflictError("Transcript refresh needs the original completed catalogue")
            receipt, body = found
            if receipt["status"] == 404:
                if rows or completed["transcripts"] != 0:
                    raise ConflictError("Completed transcript catalogue differs from retained evidence")
                break
            items, more = event_page(body, len(rows))
            rows.extend(items)
            if len(rows) > MAX_EVENTS or len({r["id"] for r in rows}) != len(rows):
                raise ConflictError("Completed transcript catalogue is ambiguous")
            if not more:
                break
        else:
            raise ResourceLimitError("Completed transcript catalogue exceeds its page bound")
        gaps = {g["event_id"] for g in completed.get("unavailable_transcripts", [])}
        calls = [r for r in rows if r["hasTranscript"] and r["id"] not in gaps]
        if len(calls) != completed["transcripts"]:
            raise ConflictError("Completed call count differs from its original catalogue")
        for event in calls:
            key = call_key(symbol, event)
            if key in known and known[key] != event["id"]:
                raise ConflictError("Completed fiscal call identity is ambiguous")
            known[key] = event["id"]
    # A removed ticker may retain completed calls before its unfinished call.
    # They remain part of the global count when the selected roster finishes.
    for symbol, saved in sorted(state.get("deferred_progress", {}).items()):
        progress=saved["progress"]
        events=progress.get("events",[])
        index=progress.get("event_index",0)
        if (not isinstance(events,list) or type(index) is not int or not 0<=index<=len(events)
            or len(events)>MAX_EVENTS):
            raise ConflictError("Deferred transcript call cursor is invalid")
        gaps={g["event_id"] for g in progress.get("unavailable_transcripts",[])}
        calls=[e for e in events[:index] if e["id"] not in gaps]
        if len(calls)!=progress.get("captured",0):
            raise ConflictError("Deferred call count differs from its saved cursor")
        for event in calls:
            pages=[];offset=0
            for _ in range(MAX_PAGES):
                path=(f"/v1/stocks/{symbol}/earnings-calls/{event['fiscalYear']}/{event['fiscalQuarter']}"
                      f"/speakers?limit=200&offset={offset}")
                found=retained(root,path)
                if found is None or found[0]["status"]!=200:
                    raise ConflictError("Deferred completed call requires its original retained pages")
                receipt,body=found
                if _utc(receipt["captured_at"])>=cutoff:
                    raise ConflictError("Catalogue epoch must follow deferred call acquisition")
                page=transcript_page(body,symbol=symbol,event=event,offset=offset)
                pages.append(page_from(root,path))
                if not page["hasMore"]:break
                offset+=page["turnCount"]
            else:
                raise ResourceLimitError("Deferred completed transcript exceeds its page bound")
            validate_bundle(symbol,saved["instrument_id"],event,tuple(pages))
            key=call_key(symbol,event)
            if key in known:
                raise ConflictError("Deferred fiscal call duplicates another saved subject")
            known[key]=event["id"]
    if len(known) != state["transcripts"]:
        raise ConflictError("Completed transcript total cannot be reconciled for refresh")
    return known


def validate_epoch(state):
    epoch = state.get("catalogue_epoch")
    if epoch is None:
        return
    if state["version"] != 2 or not isinstance(epoch, dict) or set(epoch) != {
        "id", "started_at", "previous_checkpoint", "known_calls"
    }:
        raise ValidationError("Transcript catalogue epoch is invalid")
    if not isinstance(epoch["id"], str) or not re.fullmatch(r"[a-f0-9]{64}", epoch["id"]):
        raise ValidationError("Transcript catalogue epoch identity is invalid")
    _utc(epoch["started_at"])
    if not re.fullmatch(r"transitions/[a-f0-9]{64}\.catalogue-prior\.json", epoch["previous_checkpoint"]):
        raise ValidationError("Transcript catalogue predecessor is invalid")
    calls = epoch["known_calls"]
    if not isinstance(calls, dict) or len(calls) != state["transcripts"]:
        raise ConflictError("Transcript catalogue known-call ledger differs from its count")
    for key, event_id in calls.items():
        if (not isinstance(key, str) or not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,19}:\d{4}:[1-4]", key)
            or not isinstance(event_id, str) or not 1 <= len(event_id) <= 256):
            raise ValidationError("Transcript catalogue known-call identity is invalid")


def begin_catalogue_epoch(root, *, started_at):
    """One explicit UTC observation epoch, only after the prior walk completes."""
    from .equibles_transcript_backfill import job_lock, read_file, private_directory, atomic
    at = _utc(started_at)
    with job_lock(root):
        body = read_file(root/"state.json", 32*1024*1024)
        state = loads_strict(body, max_bytes=32*1024*1024)
        validate_checkpoint(state)
        if state["version"] != 2:
            raise ConflictError("Catalogue refresh requires the paid selected checkpoint")
        identity = hashlib.sha256(dumps_strict({
            "scope": state["selection"]["scope_sha256"], "observed_at": at
        }).encode()).hexdigest()
        prior = state.get("catalogue_epoch")
        if prior is not None and prior["id"] == identity:
            return {"outcome": "unchanged", "epoch_id": identity, "provider_requests": 0}
        previous_at = prior["started_at"] if prior is not None else state["created_at"]
        if (at <= _utc(previous_at)
            or at <= _utc(state.get("selection_transition",{}).get("transitioned_at",state["created_at"]))
            or any(at <= _utc(row["finished_at"]) for row in state["completed"].values())):
            raise ConflictError("New catalogue epoch must follow all prior completed work")
        if (state["index"] != len(state["roster"]) or state["current"] is not None
            or state["pending"] is not None or state["blocked"] is not None):
            raise ConflictError("Finish or reconcile the current transcript walk before refreshing")
        known = _known_calls(root, state, cutoff=at)
        previous_sha = hashlib.sha256(body).hexdigest()
        reference = "transitions/" + previous_sha + ".catalogue-prior.json"
        symbols = {r["symbol"] for r in state["roster"]}
        state["completed"] = {k: v for k, v in state["completed"].items() if k not in symbols}
        state.update(index=0, current=None, catalogue_epoch={
            "id": identity, "started_at": at, "previous_checkpoint": reference, "known_calls": known
        })
        validate_checkpoint(state)
        raw = dumps_strict(state, max_bytes=32*1024*1024).encode()
        private_directory(root/"transitions")
        atomic(root/reference, body)
        atomic(root/"transitions"/(identity+".catalogue.json"), {
            "contract": "quant_data.equibles_catalogue_epoch.v1",
            "epoch_id": identity, "started_at": at, "previous_checkpoint_sha256": previous_sha,
            "scope_sha256": state["selection"]["scope_sha256"], "subjects": len(symbols),
            "known_calls": len(known), "provider_requests": 0,
            "per_invocation": POLICY, "recurring_unit_changes": 0
        })
        atomic(root/"state.json", raw, replace=True)
        return {"outcome": "prepared", "epoch_id": identity, "subjects": len(symbols),
                "known_calls": len(known), "provider_requests": 0}


def continuation_decision(report, *, observed_at):
    """No sleep or scheduler mutation: decide whether NEW remaining work can run."""
    at = _utc(observed_at)
    if not isinstance(report, dict) or report.get("contract") != "quant_data.equibles_transcript_backfill.v2":
        raise ValidationError("Paid continuation requires its versioned report")
    outcome = report.get("outcome")
    if outcome in ("run_resource_limit", "runtime_limit"):
        return {"action": "continue", "reason": "unfinished_new_work", "not_before": at}
    if outcome == "day_or_runtime_limit":
        return {"action": "continue", "reason": "new_invocation_budget", "not_before": at}
    if outcome in ("daily_quota", "provider_quota"):
        quota = report.get("quota") or {}
        reset = quota.get("reset")
        if type(reset) is not int or reset <= datetime.fromisoformat(at.replace("Z","+00:00")).timestamp():
            return {"action": "review", "reason": "quota_reset_unverified", "not_before": None}
        return {"action": "defer", "reason": outcome, "not_before":
                datetime.fromtimestamp(reset, timezone.utc).isoformat().replace("+00:00","Z")}
    if outcome == "complete":
        return {"action": "complete", "reason": "catalogue_walk_complete", "not_before": None}
    return {"action": "review", "reason": outcome or "unknown_outcome", "not_before": None}


def run_continuations(root, publisher, transport, *, max_invocations, max_requests,
        max_run_seconds, max_total_bytes, clock, monotonic, sleeper, requests_per_invocation=1000):
    """Continue only unfinished new work inside one explicitly finite allocation."""
    from .equibles_transcript_backfill import run, job_lock
    bounds = ((max_invocations,24), (max_requests,100000),
              (requests_per_invocation,POLICY["max_requests_per_run"]),
              (max_run_seconds,86400), (max_total_bytes,24*POLICY["max_total_bytes"]))
    if any(type(value) is not int or not 1 <= value <= cap for value,cap in bounds):
        raise ValidationError("Transcript continuation allocation is invalid")
    started=monotonic();requests=received=0;reports=[]
    with job_lock(root/"continuations"):
        for index in range(max_invocations):
            seconds=int(max_run_seconds-(monotonic()-started))
            remaining_requests=max_requests-requests
            remaining_bytes=max_total_bytes-received
            if seconds<=35 or remaining_requests<1 or remaining_bytes<8*1024*1024:
                break
            report=run(root,publisher,transport,clock=clock,monotonic=monotonic,sleeper=sleeper,
                allow_paid=True,max_requests=min(requests_per_invocation,remaining_requests),
                max_run_seconds=min(POLICY["max_run_seconds"],seconds),
                max_total_bytes=min(POLICY["max_total_bytes"],remaining_bytes),
                deadline=started+max_run_seconds)
            reports.append(report)
            requests+=report["requests_this_run"];received+=report["response_bytes_this_run"]
            decision=continuation_decision(report,observed_at=clock().isoformat())
            if decision["action"]!="continue":
                return {"outcome":report["outcome"],"requests":requests,"received_bytes":received,
                    "invocations":len(reports),"continuation":decision,"last_report":report}
            if monotonic()-started>=max_run_seconds:break
            # Zero progress cannot form an automatic retry/spin loop.
            if report["requests_this_run"]==0:
                return {"outcome":"no_new_work_progress","requests":requests,"received_bytes":received,
                    "invocations":len(reports),"continuation":{"action":"review",
                        "reason":"no_new_work_progress","not_before":None},"last_report":report}
    return {"outcome":"continuation_allocation_exhausted","requests":requests,
        "received_bytes":received,"invocations":len(reports),
        "last_report":reports[-1] if reports else None}
