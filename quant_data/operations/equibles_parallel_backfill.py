"""Bounded parallel Equibles acquisition followed by the existing serial publisher."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
import math
import json
import threading
import time

from ..errors import ConflictError, ValidationError
from . import equibles_transcript_backfill as job
from . import equibles_failure_policy as failure_policy
from .equibles_paid_policy import quota_bucket, apply_paid_headers, validate_checkpoint
from ..company.equibles_transcripts import transcript_page, event_page, validate_symbol

WORKERS = 4
SPACING_SECONDS = 1
DAILY_CAP = 10000
GUARD = "parallel_acquisition_pending"


def planned_paths(state, count=WORKERS):
    """Current ticker only; later pages depend on the stored current offset."""
    if state["index"] >= len(state["roster"]):
        return []
    symbol = validate_symbol(state["roster"][state["index"]]["symbol"])
    current = state["current"]
    if current is None or not current["catalog_done"]:
        offset = len((current or {}).get("catalog", []))
        return [f"/v1/stocks/{symbol}/investor-events?eventType=EarningsCall&limit=100&offset={offset}"]
    result = []
    for index, event in enumerate(current["events"][current["event_index"]:]):
        offset = current["offset"] if index == 0 else 0
        result.append(f"/v1/stocks/{symbol}/earnings-calls/{event['fiscalYear']}/{event['fiscalQuarter']}/speakers?limit=200&offset={offset}")
        if len(result) == count:
            break
    return result


def _validate_body(state, path, body):
    symbol = state["roster"][state["index"]]["symbol"]
    if "/investor-events?" in path:
        event_page(body, int(path.rsplit("offset=", 1)[1]))
        return
    parts = path.split("/")
    year, quarter = int(parts[5]), int(parts[6])
    event = next(e for e in state["current"]["events"]
                 if e["fiscalYear"] == year and e["fiscalQuarter"] == quarter)
    offset = int(path.rsplit("offset=", 1)[1])
    if not job.empty_transcript_page(body, symbol=symbol, event=event, offset=offset):
        transcript_page(body, symbol=symbol, event=event, offset=offset)


def _settle(root, state, journal, *, clock):
    """Replay only retained results; never dispatch an uncertain reservation."""
    failures = []
    failure_details = []
    undispatched = []
    quota_deferred = False
    for entry in journal["entries"]:
        found = job.response_receipt(root, entry["attempt"], entry["path"])
        if found is None:
            marker=root/"attempts"/(entry["attempt"]+".not-dispatched.json")
            if marker.exists():
                value=json.loads(job.read_file(marker,65536))
                if value.get("reservation")==entry and value.get("outcome")=="not_dispatched":
                    undispatched.append(entry)
                    continue
            diagnostic = job.retained_transport_failure(root, entry)
            if diagnostic is not None:
                failure_details.append({"attempt": entry["attempt"], "path": entry["path"], **diagnostic})
            retry_diagnostic = diagnostic or {"category": "uncertain_attempt", "http_status": None}
            if failure_policy.fail(state, entry, retry_diagnostic, observed_at=clock().isoformat()):
                continue
            failures.append(entry)
            continue
        receipt, body = found
        if receipt["captured_at"] < entry["started_at"]:
            failures.append(entry)
            continue
        headers = receipt["headers"]
        try:
            status = receipt["status"]
            retryable_http = failure_policy.enabled(state) and status in failure_policy.RETRYABLE_HTTP
            if status not in (200, 404, 429) and not retryable_http:
                raise ValidationError("Equibles parallel provider failure")
            if status == 200:
                if "application/json" not in headers.get("content-type", "").lower():
                    raise ValidationError("Equibles parallel content type differs")
                _validate_body(state, entry["path"], body)
            bucket = quota_bucket(state, journal["day"])
            fields = ("x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset")
            if (status == 404 or retryable_http) and not any(k in headers for k in fields):
                pass  # A missing-header 404 cannot erase already verified same-day quota.
            else:
                limit, remaining, reset = (int(headers[k]) for k in fields)
                boundary = datetime.fromisoformat(journal["day"]).replace(tzinfo=timezone.utc) + timedelta(days=1)
                if limit < 1 or not 0 <= remaining <= limit or reset != int(boundary.timestamp()):
                    raise ValidationError("Equibles parallel quota window differs")
                prior = bucket["remaining"]
                observed = bucket.get("provider_observed_at")
                # The lowest allowance wins even when response completion is reordered.
                if observed is None or receipt["captured_at"] >= observed:
                    apply_paid_headers(state, bucket, limit=limit, remaining=remaining, reset=reset,
                        receipt_at=receipt["captured_at"], reservation_at=entry["started_at"])
                bucket["remaining"] = min(prior, remaining, DAILY_CAP-bucket["attempted"])
            if retryable_http:
                failure_policy.fail(state, entry, {"category": "http_failure", "http_status": status,
                    "response_sha256": receipt["sha256"]}, observed_at=receipt["captured_at"])
            elif status == 429:
                bucket["remaining"]=0
                quota_deferred=True
            else:
                job.atomic(root/"responses"/(job.request_id(entry["path"])+".json"), receipt)
                failure_policy.succeeded(state, entry)
        except (ValueError, KeyError, StopIteration, ValidationError, ConflictError):
            failures.append(entry)
    bucket=quota_bucket(state,journal["day"])
    verification_undispatched=sum(bool(e.get("verification_reserved")) for e in undispatched)
    bucket["verification_requests"]=min(bucket["verification_requests"],
        journal["verification_reserved_through"]-verification_undispatched)
    state["undispatched_reservations"]=max(state.get("undispatched_reservations",0),
        journal["undispatched_base"]+len(undispatched))
    state["pending"] = failures[0] if failures else None
    state["blocked"] = ({"error": "ParallelAcquisitionError", "reason": "retained_failure_or_uncertain_attempt",
                         "at": job.utc(clock().isoformat())} if failures else None)
    if failure_details and failures:
        state["blocked"]["transport_failures"] = failure_details
    journal["outcome"] = "blocked" if failures else "quota_deferred" if quota_deferred else "settled"
    job.atomic(root/"parallel"/"current.json", journal, replace=True)
    job.atomic(root/"state.json", state, replace=True)
    return not failures


def recover_batch(root, *, clock=job.now):
    with job.job_lock(root):
        path = root/"parallel"/"current.json"
        if not path.exists():
            return True
        journal = json.loads(job.read_file(path, 65536))
        state = json.loads(job.read_file(root/"state.json", 32*1024*1024))
        if journal["outcome"] in ("settled","quota_deferred") and not state.get("blocked"):
            return True
        if state["usage"][journal["day"]]["attempted"] < journal["charged_through"]:
            raise ConflictError("Parallel reservation ledger needs reconciliation")
        if not state.get("blocked") or state["blocked"]["reason"] not in {
                GUARD, "retained_failure_or_uncertain_attempt"}:
            raise ConflictError("Parallel journal has no matching checkpoint guard")
        return _settle(root, state, journal, clock=clock)


def acquire_batch(root, transport, *, max_requests, max_bytes, deadline,
                  day, clock=job.now, monotonic=time.monotonic, sleeper=time.sleep):
    """One owner reserves/persists; network-only callers overlap outside store locks."""
    if (type(max_requests) is not int or not 1 <= max_requests <= 1000
            or type(max_bytes) is not int or not 1 <= max_bytes <= 400*1024*1024
            or type(deadline) not in (int,float) or not math.isfinite(deadline)):
        raise ValidationError("Parallel acquisition bounds are invalid")
    with job.job_lock(root):
        state = json.loads(job.read_file(root/"state.json", 32*1024*1024))
        validate_checkpoint(state)
        if state["pending"] or state["blocked"]:
            raise ConflictError("Equibles outstanding work must be reconciled first")
        bucket = quota_bucket(state, day)
        from .equibles_paid_policy import operating_daily_cap
        count = min(WORKERS, max_requests, max_bytes//job.MAX_BYTES,
                    operating_daily_cap(state, DAILY_CAP)-bucket["attempted"], bucket["remaining"])
        candidates = [p for p in planned_paths(state, max(1, count))
                      if job.retained(root, p) is None and failure_policy.unresolved(state, p) is None]
        if not bucket["paid_headers_verified"]:
            retry_probe = bool(candidates and failure_policy.retry_of(state, candidates[0]) is not None
                               and bucket["verification_requests"] == 1)
            count = min(count, 1 if bucket["verification_requests"] == 0 or retry_probe else 0)
        paths = candidates[:count]
        if not paths or clock().astimezone(timezone.utc).date().isoformat() != day or monotonic() >= deadline-35:
            outcome=("quota_verification_required" if not bucket["paid_headers_verified"] and bucket["verification_requests"]>=1
                else "daily_quota" if count<=0 else "day_or_runtime_limit")
            return {"requests": 0, "bytes": 0, "outcome": outcome, "max_inflight": 0}
        private = root/"parallel"
        job.private_directory(private)
        entries = []
        for path in paths:
            at = job.utc(clock().isoformat())
            identifier = day+"-"+str(bucket["attempted"]+1).zfill(3)
            entry = {"path": path, "attempt": identifier, "started_at": at}
            failure_policy.reserve_retry(state, entry, day)
            bucket["attempted"] += 1
            bucket["remaining"] -= 1
            if not bucket["paid_headers_verified"]:
                bucket["verification_requests"] += 1
                entry["verification_reserved"] = True
            job.atomic(root/"attempts"/(identifier+".json"), entry)
            entries.append(entry)
        journal = {"version": 1, "day": day, "entries": entries, "outcome": "reserved",
                   "charged_through": bucket["attempted"],
                   "verification_reserved_through":bucket["verification_requests"],
                   "undispatched_base":state.get("undispatched_reservations",0)}
        job.atomic(private/(entries[0]["attempt"]+".reserved.json"), journal)
        job.atomic(private/"current.json", journal, replace=True)
        state["pending"] = entries[0]
        state["blocked"] = {"error": "ParallelReservation", "reason": GUARD,
                            "at": job.utc(clock().isoformat())}
        state["last_request_at"] = entries[-1]["started_at"]
        job.atomic(root/"state.json", state, replace=True)
        lock = threading.Lock()
        counter_lock = threading.Lock()
        last_start = [monotonic()]
        active = [0, 0]
        received = [0]
        stop = threading.Event()

        def fetch(entry):
            with lock:
                delay = max(0, last_start[0]+SPACING_SECONDS-monotonic())
                if delay:
                    sleeper(delay)
                # Avoid assigning an invented time when the host wall clock steps back.
                while job.utc(clock().isoformat()) < entry["started_at"] and monotonic() < deadline-30:
                    sleeper(0.05)
                if (stop.is_set() or monotonic() >= deadline-30
                        or clock().astimezone(timezone.utc).date().isoformat() != day):
                    return entry, "not_dispatched"
                last_start[0] = monotonic()
                with counter_lock:
                    active[0] += 1
                    active[1] = max(active[1], active[0])
            try:
                result = transport.request(entry["path"])
                while job.utc(clock().isoformat()) < entry["started_at"] and monotonic() < deadline:
                    sleeper(0.05)
                at = job.utc(clock().isoformat())
                status, headers, body = result
                if status not in (200, 404):
                    stop.set()
                return entry, (status, headers, body, at)
            except Exception as exc:
                stop.set()
                failure = exc if isinstance(exc, job.EquiblesTransportFailure) else job.EquiblesTransportFailure("transport_error")
                return entry, failure
            finally:
                with counter_lock:
                    active[0] -= 1

        # All reservations are durable before starting any network caller.
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = [pool.submit(fetch, entry) for entry in entries]
            for future in as_completed(futures):
                entry, result = future.result()
                if result == "not_dispatched":
                    job.atomic(root/"attempts"/(entry["attempt"]+".not-dispatched.json"),
                        {"outcome":"not_dispatched","reservation":entry,"deferred_at":job.utc(clock().isoformat())})
                    continue
                if isinstance(result, job.EquiblesTransportFailure):
                    job.retain_transport_failure(root, entry, result, clock().isoformat())
                    continue
                if result is None:
                    continue
                status, headers, body, at = result
                if not isinstance(body, bytes) or len(body) > job.MAX_BYTES:
                    stop.set()
                    continue
                received[0] += len(body)
                job.retain_response(root, entry["path"], body, at, headers, status, entry["attempt"])
                try:
                    if at < entry["started_at"] or status not in (200,404):
                        raise ValidationError("Parallel response cannot be accepted")
                    if status == 200:
                        if "application/json" not in headers.get("content-type","").lower():
                            raise ValidationError("Parallel response content type differs")
                        _validate_body(state,entry["path"],body)
                    keys=("x-ratelimit-limit","x-ratelimit-remaining","x-ratelimit-reset")
                    if status == 200 or any(k in headers for k in keys):
                        limit,remaining,reset=(int(headers[k]) for k in keys)
                        reset_expected=int((datetime.fromisoformat(day).replace(tzinfo=timezone.utc)+timedelta(days=1)).timestamp())
                        if limit<1 or not 0<=remaining<=limit or reset!=reset_expected:
                            raise ValidationError("Parallel response quota differs")
                        # Fresh account usage may exhaust allowance after reservation.
                        # Later, out-of-order headers must never reopen queued work.
                        if remaining < sum(not pending.done() for pending in futures):
                            stop.set()
                except (ValueError,KeyError,StopIteration,ValidationError,ConflictError):
                    stop.set()
        settled = _settle(root, state, journal, clock=clock)
        summary = {"requests": len(entries), "bytes": received[0],
                   "outcome": ("provider_quota" if journal["outcome"]=="quota_deferred" else "acquired") if settled else "blocked", "max_inflight": active[1]}
        job.atomic(private/(entries[0]["attempt"]+".batch.json"),
                   {**journal, **summary})
        return summary


def run_wave(root, publisher, transport, *, clock=job.now, monotonic=time.monotonic,
             sleeper=time.sleep, allow_paid=True, max_requests=1000,
             max_run_seconds=3600, max_total_bytes=400*1024*1024, deadline=None):
    if (type(max_requests) is not int or not 1 <= max_requests <= 1000
            or type(max_run_seconds) is not int or not 1 <= max_run_seconds <= 3600
            or type(max_total_bytes) is not int or not 1 <= max_total_bytes <= 400*1024*1024):
        raise ValidationError("Parallel invocation exceeds its policy bounds")
    entered = monotonic()
    deadline = min(entered+max_run_seconds, deadline if deadline is not None else entered+max_run_seconds)
    day = clock().astimezone(timezone.utc).date().isoformat()
    requests = received = peak = 0
    if not recover_batch(root, clock=clock):
        state = json.loads(job.read_file(root/"state.json", 32*1024*1024))
        return job.run(root, publisher, transport, clock=clock, monotonic=monotonic,
                       sleeper=sleeper, allow_paid=True, cache_only=True)
    while True:
        report = job.run(root, publisher, transport, clock=clock, monotonic=monotonic,
            sleeper=sleeper, allow_paid=True, max_run_seconds=max(1, min(3600, int(deadline-monotonic()))),
            deadline=deadline, cache_only=True)
        if report["outcome"] != "cache_exhausted":
            break
        from .equibles_catalogue import continuation_decision
        progress={**report,"outcome":"run_resource_limit",
                  "requests_this_run":requests,"response_bytes_this_run":received,
                  "parallel_callers":WORKERS,"max_inflight":peak}
        progress["continuation"]=continuation_decision(progress,observed_at=clock().isoformat())
        job.atomic(root/"status.json",progress,replace=True)
        if requests >= max_requests or received+job.MAX_BYTES > max_total_bytes or monotonic() >= deadline-35:
            report["outcome"] = "run_resource_limit"
            break
        batch = acquire_batch(root, transport, max_requests=max_requests-requests,
            max_bytes=max_total_bytes-received, deadline=deadline, day=day,
            clock=clock, monotonic=monotonic, sleeper=sleeper)
        requests += batch["requests"]
        received += batch["bytes"]
        peak = max(peak, batch["max_inflight"])
        if batch["outcome"] in ("blocked","provider_quota"):
            report["outcome"] = batch["outcome"]
            report["blocked"] = json.loads(job.read_file(root/"state.json", 32*1024*1024))["blocked"]
            break
        if batch["requests"] == 0:
            report["outcome"] = batch["outcome"]
            break
    from .equibles_catalogue import continuation_decision
    state=json.loads(job.read_file(root/"state.json",32*1024*1024))
    report["quota"]=state["usage"].get(day)
    report["blocked"]=state["blocked"]
    report.update(failure_policy.stats(state, day))
    report["continuation"]=continuation_decision(report,observed_at=clock().isoformat())
    report.update(requests_this_run=requests, response_bytes_this_run=received,
                  parallel_callers=WORKERS, max_inflight=peak)
    job.atomic(root/"status.json", report, replace=True)
    return report
