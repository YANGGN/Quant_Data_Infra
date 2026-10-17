"""Fixed, finite, ticker-by-ticker Equibles backfill with durable quota accounting."""
from __future__ import annotations

from ..ingestion import PublicationDeferred

from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import fcntl
import hashlib
import http.client
import json
import math
import multiprocessing
import os
from pathlib import Path
import stat
import sys
import time

from ..company.equibles_transcripts import (
    EquiblesTranscriptPublisher, RawPage, MAX_BYTES, MAX_EVENTS, MAX_PAGES,
    event_page, transcript_page, validate_bundle, validate_symbol, utc,
)
from ..credentials import read_project_credential
from ..errors import ConflictError, ResourceLimitError, ValidationError
from ..json_codec import dumps_strict
from ..registry import CANONICAL_REGISTRY_PATH, load_registry
from ..stores import StoreMap, quiet_immutable_read_connection

PROJECT_ROOT = Path("/home/volatility/Python_Projects/Quant_Data_Infra")
STATE_ROOT = PROJECT_ROOT / "data/.operations/equibles-transcripts"
DAILY_CAP = 100
MAX_TICKERS = 519
MAX_RUN_SECONDS = 3600
MAX_TOTAL_BYTES = 400 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 30
HEADERS = frozenset(("content-type", "x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset"))


class EquiblesTransportFailure(ResourceLimitError):
    """A bounded diagnostic with no provider body, credential, or exception text."""
    CATEGORIES = frozenset(("timeout", "connection_error", "incomplete_response",
                            "response_too_large", "credential_exposure",
                            "worker_lost", "invalid_worker_response", "transport_error"))

    def __init__(self, category, response_status=None):
        if (category not in self.CATEGORIES or (response_status is not None
                and (type(response_status) is not int or not 100 <= response_status <= 599))):
            raise ValidationError("Equibles failure diagnostic is invalid")
        self.category = category
        self.response_status = response_status
        super().__init__("Equibles request failed: " + category)

    def diagnostic(self):
        return {"contract": "quant_data.equibles_transport_failure.v1",
                "category": self.category, "http_status": self.response_status}


def transport_failure(value):
    if (not isinstance(value, dict) or set(value) != {"contract", "category", "http_status"}
            or value["contract"] != "quant_data.equibles_transport_failure.v1"):
        raise ValidationError("Equibles failure diagnostic is invalid")
    return EquiblesTransportFailure(value["category"], value["http_status"])


def retain_transport_failure(root, entry, error, captured_at):
    failure = error if isinstance(error, EquiblesTransportFailure) else EquiblesTransportFailure("transport_error")
    atomic(root / "attempts" / (entry["attempt"] + ".failure.json"),
           {"reservation": entry, "captured_at": utc(captured_at), "failure": failure.diagnostic()})


def retained_transport_failure(root, entry):
    path = root / "attempts" / (entry["attempt"] + ".failure.json")
    if not path.exists():
        return None
    value = json.loads(read_file(path, 65536))
    if (set(value) != {"reservation", "captured_at", "failure"}
            or value["reservation"] != entry or utc(value["captured_at"]) < entry["started_at"]):
        raise ConflictError("Equibles failure receipt differs from its reservation")
    return transport_failure(value["failure"]).diagnostic()


def now():
    return datetime.now(timezone.utc)


def stores():
    return StoreMap.from_mapping({r: PROJECT_ROOT / "data" / (r + ".sqlite")
                                 for r in ("market", "macro", "company", "news")})


def private_directory(path):
    if not path.is_absolute() or path.resolve() != path:
        raise ValidationError("Equibles private path is not resolved")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700:
        raise ConflictError("Equibles private directory conflicts")


def fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def read_file(path, bound):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > bound:
        raise ConflictError("Equibles retained file conflicts")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW), "rb") as handle:
        body = handle.read(bound + 1)
    if len(body) > bound:
        raise ResourceLimitError("Equibles retained file exceeds bound")
    return body


def atomic(path, body, *, replace=False):
    if not isinstance(body, bytes):
        body = dumps_strict(body).encode()
    if path.exists():
        old = read_file(path, max(MAX_BYTES, 32 * 1024 * 1024))
        if old == body:
            return
        if not replace:
            raise ConflictError("Equibles immutable receipt differs")
    temporary = path.with_name(path.name + ".tmp-" + str(os.getpid()))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


@contextmanager
def job_lock(root):
    private_directory(root)
    path = root / "job.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600:
            raise ConflictError("Equibles job lock conflicts")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ConflictError("Equibles backfill is already running") from exc
        yield
    finally:
        os.close(fd)


def request_id(path):
    return hashlib.sha256(path.encode()).hexdigest()


def retain_response(root, path, body, captured_at, headers, status, identifier=None):
    digest = hashlib.sha256(body).hexdigest()
    atomic(root / "blobs" / (digest + ".json"), body)
    receipt = {"path": path, "sha256": digest, "captured_at": utc(captured_at),
               "headers": {k.lower(): str(v) for k,v in headers.items() if k.lower() in HEADERS},
               "status": status, "bytes": len(body)}
    target = root / "responses" / ((identifier or request_id(path)) + ".json")
    atomic(target, receipt)
    return receipt


def response_receipt(root, identifier, path):
    target = root / "responses" / (identifier + ".json")
    if not target.exists():
        return None
    receipt = json.loads(read_file(target, 65536))
    digest = receipt.get("sha256")
    import re
    if receipt.get("path") != path or not isinstance(digest, str) or not re.fullmatch("[a-f0-9]{64}", digest):
        raise ConflictError("Equibles cached scope differs")
    body = read_file(root / "blobs" / (digest + ".json"), MAX_BYTES)
    if len(body) != receipt["bytes"] or hashlib.sha256(body).hexdigest() != digest:
        raise ConflictError("Equibles cached bytes differ")
    return receipt, body


def retained(root, path, *, identifier=None):
    found = response_receipt(root, identifier or request_id(path), path)
    if found:
        status = found[0].get("status")
        if status not in (200, 404):
            raise ConflictError("Equibles cached response is not reusable")
    return found


def empty_transcript_page(body, *, symbol, event, offset):
    """Recognize an explicitly empty first page without accepting it as a call."""
    from ..company.equibles_transcripts import document, integer

    # A literal JSON null is explicit provider absence, tied to the retained
    # request path. It cannot complete a partially fetched transcript.
    if (isinstance(body, bytes) and len(body) <= MAX_BYTES
            and body.strip(b" \t\r\n") == b"null"):
        validate_symbol(symbol)
        if offset != 0:
            raise ValidationError("Equibles null transcript follows partial pages")
        return True
    value = document(body)
    if "data" not in value or value["data"] not in ([], None):
        return False
    if (value.get("ticker") != validate_symbol(symbol)
            or value.get("eventId") != event["id"]
            or integer(value.get("fiscalYear"), 1900, 2200) != event["fiscalYear"]
            or integer(value.get("fiscalQuarter"), 1, 4) != event["fiscalQuarter"]
            or offset != 0 or integer(value.get("offset"), 0, 10000) != 0
            or integer(value.get("turnCount"), 0, 200) != 0
            or integer(value.get("totalTurnCount"), 0, MAX_PAGES * 200) != 0
            or value.get("hasMore") is not False):
        raise ValidationError("Equibles empty transcript scope or pagination is invalid")
    return True


def page_from(root, path):
    receipt, body = retained(root, path)
    return RawPage(body, receipt["captured_at"],
        "equibles-transcripts/blobs/" + receipt["sha256"] + ".json", dumps_strict(receipt["headers"]))


def freeze(root, roster, *, evaluation_root=None):
    """Offline one-time setup. Evaluation receipts seed both cache and shared quota."""
    with job_lock(root):
        if (root / "state.json").exists():
            raise ConflictError("Equibles universe is already frozen")
        for name in ("blobs", "responses", "attempts"):
            private_directory(root / name)
        if not roster or len(roster) > MAX_TICKERS:
            raise ResourceLimitError("Equibles universe exceeds its bound")
        roster = sorted(roster, key=lambda r: r["symbol"])
        if len({r["symbol"] for r in roster}) != len(roster):
            raise ConflictError("Equibles universe has duplicate symbols")
        for row in roster:
            validate_symbol(row["symbol"])
            if not isinstance(row.get("instrument_id"), str) or not row["instrument_id"]:
                raise ValidationError("Equibles instrument binding is invalid")
        usage, cached = {}, 0
        if evaluation_root:
            receipts = [json.loads(line) for line in (evaluation_root / "results.jsonl").read_text().splitlines()]
            for item in receipts:
                headers = {k.lower(): str(v) for k,v in item["headers"].items() if k.lower() in HEADERS}
                day = utc(item["startedAt"])[:10]
                bucket = usage.setdefault(day, {"attempted": 0, "remaining": DAILY_CAP})
                bucket["attempted"] += 1
                bucket["remaining"] = min(bucket["remaining"], int(headers["x-ratelimit-remaining"]))
                path = item["path"]
                if item["status"] == 200 and ("/investor-events?" in path or "/speakers?" in path):
                    body = (evaluation_root / item["responseFile"]).read_bytes()
                    if hashlib.sha256(body).hexdigest() != item["sha256"]:
                        raise ConflictError("Equibles evaluation evidence changed")
                    retain_response(root, path, body, item["finishedAt"], headers, 200)
                    cached += 1
        state = {"version": 1, "roster": roster, "created_at": utc(now().isoformat()),
                 "index": 0, "completed": {}, "current": None, "usage": usage,
                 "pending": None, "blocked": None, "transcripts": 0, "seeded_responses": cached}
        atomic(root / "state.json", state)
        return {"tickers": len(roster), "seeded_responses": cached, "usage": usage}


def freeze_live_universe():
    with quiet_immutable_read_connection(stores(), "market") as c:
        rows = c.execute("SELECT provider_symbol,instrument_id FROM stage10_instruments "
                         "WHERE provider='fmp' AND asset_type='equity' ORDER BY provider_symbol LIMIT ?",
                         (MAX_TICKERS + 1,)).fetchall()
    if len(rows) != MAX_TICKERS:
        raise ConflictError("Equibles retained 519-ticker universe changed")
    return freeze(STATE_ROOT, [{"symbol": r[0], "instrument_id": r[1]} for r in rows],
                  evaluation_root=PROJECT_ROOT / ".local/equibles-evaluation-20260907")


def _http_child(sender, path, key):
    status = None
    category = "connection_error"
    try:
        connection = http.client.HTTPSConnection("api.equibles.com", timeout=20)
        try:
            connection.request("GET", path, headers={"Authorization": "Bearer " + key,
                "Accept": "application/json", "User-Agent": "QuantDataInfra/1.0"})
            response = connection.getresponse()
            status = response.status
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                category = "response_too_large"
                raise ValueError()
            if key.encode() in body:
                category = "credential_exposure"
                raise ValueError()
            headers = {k.lower(): v for k,v in response.getheaders() if k.lower() in HEADERS}
            if any(key in v for v in headers.values()):
                category = "credential_exposure"
                raise ValueError()
            sender.send((status, headers, body))
        finally:
            connection.close()
    except Exception as exc:
        if isinstance(exc, TimeoutError):
            category = "timeout"
        elif isinstance(exc, http.client.IncompleteRead):
            category = "incomplete_response"
        sender.send(EquiblesTransportFailure(category, status).diagnostic())
    finally:
        sender.close()


class EquiblesTransport:
    """One GET, no redirects/retries, and a hard 30-second process deadline."""
    def __init__(self, key, *, start_method="fork"):
        self.key = key
        self.start_method = start_method

    def request(self, path):
        # Only the two fixed endpoint shapes produced by this module are permitted.
        import re
        if not re.fullmatch(r"/v1/stocks/[A-Z0-9][A-Z0-9.\-]{0,19}/(?:investor-events\?eventType=EarningsCall&limit=100&offset=\d{1,4}|earnings-calls/\d{4}/[1-4]/speakers\?limit=200&offset=\d{1,5})", path):
            raise ValidationError("Equibles request route is invalid")
        context = multiprocessing.get_context(self.start_method)
        receiver, sender = context.Pipe(duplex=False)
        child = context.Process(target=_http_child, args=(sender, path, self.key))
        deadline = time.monotonic() + REQUEST_TIMEOUT_SECONDS
        child.start()
        sender.close()
        try:
            from .collection_transport import _receive_until
            try:
                result = _receive_until(receiver, deadline, MAX_BYTES)
            except ResourceLimitError:
                raise EquiblesTransportFailure("timeout") from None
            if isinstance(result, dict):
                try:
                    failure = transport_failure(result)
                except (ValidationError, TypeError):
                    raise EquiblesTransportFailure("invalid_worker_response") from None
                raise failure
            if not isinstance(result, tuple) or len(result) != 3:
                raise EquiblesTransportFailure("worker_lost" if result is None else "invalid_worker_response")
            return result
        except (EOFError, OSError):
            raise EquiblesTransportFailure("worker_lost") from None
        finally:
            receiver.close()
            if child.is_alive():
                child.terminate()
            child.join(timeout=2)
            if child.is_alive():
                child.kill()
                child.join()


class Deferred(Exception):
    pass


def run(root, publisher, transport, *, clock=now, monotonic=time.monotonic, sleeper=time.sleep, allow_paid=False,
        max_requests=None, max_run_seconds=None, max_total_bytes=None, deadline=None, cache_only=False):
    """Internal explicit-root harness; the public executable accepts no paths or switches."""
    entered=monotonic()
    if deadline is not None and (type(deadline) not in (int,float) or not math.isfinite(deadline)):
        raise ValidationError("Transcript absolute deadline is invalid")
    with job_lock(root):
        state = json.loads(read_file(root / "state.json", 32 * 1024 * 1024))
        paid=state.get("version")==2
        if paid:
            if allow_paid is not True:
                raise ConflictError("Equibles paid checkpoint requires an explicitly activated binding")
            from .equibles_paid_policy import validate_checkpoint,quota_bucket,apply_paid_headers,POLICY
            validate_checkpoint(state)
            daily_cap=POLICY["daily_ceiling"]
            request_cap=POLICY["max_requests_per_run"]
        else:
            if state.get("version") != 1 or len(state["roster"]) > MAX_TICKERS:
                raise ConflictError("Equibles checkpoint version or universe is invalid")
            daily_cap=request_cap=DAILY_CAP
        from .equibles_paid_policy import operating_daily_cap
        daily_cap = operating_daily_cap(state, daily_cap)
        limits = (request_cap, MAX_RUN_SECONDS, MAX_TOTAL_BYTES)
        supplied = (max_requests, max_run_seconds, max_total_bytes)
        if not paid and (deadline is not None or any(value is not None for value in supplied)):
            raise ValidationError("Legacy transcript invocation bounds cannot be overridden")
        if any(value is not None and (type(value) is not int or not 1 <= value <= bound)
               for value, bound in zip(supplied, limits)):
            raise ValidationError("Paid transcript invocation exceeds its policy bounds")
        request_cap, run_seconds, total_bytes = tuple(
            bound if value is None else value for value, bound in zip(supplied, limits))
        from .equibles_catalogue import catalogue_identifier, call_key
        from . import equibles_failure_policy as failure_policy
        failure_policy.enabled(state)
        epoch = state.get("catalogue_epoch")
        if epoch is not None and utc(clock().isoformat()) < epoch["started_at"]:
            raise ConflictError("Transcript invocation predates its catalogue epoch")
        started, day, requests, received = entered, utc(clock().isoformat())[:10], 0, 0
        if deadline is not None:run_seconds=min(run_seconds,deadline-started)
        def save():
            atomic(root / "state.json", state, replace=True)
        def accept_response(path, receipt, body):
            # Also used on restart: a durable response is reconciled without another GET.
            pending = state["pending"]
            if (not pending or pending["path"] != path or (epoch is not None
                and pending.get("response_identifier") != catalogue_identifier(state,path))):
                raise ConflictError("Equibles response has no matching reservation")
            reservation_day = utc(pending["started_at"])[:10]
            bucket = state["usage"][reservation_day]
            headers, status = receipt["headers"], receipt["status"]
            receipt_at=utc(receipt["captured_at"])
            if receipt_at<utc(pending["started_at"]):
                raise ConflictError("Equibles response predates its reservation")
            header_applied=False
            missing = status == 404
            quota_headers = ("x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset")
            try:
                limit = int(headers["x-ratelimit-limit"])
                remaining = int(headers["x-ratelimit-remaining"])
                reset = int(headers["x-ratelimit-reset"])
                boundary = datetime.fromisoformat(reservation_day).replace(tzinfo=timezone.utc) + timedelta(days=1)
                boundaries={int(boundary.timestamp()):reservation_day}
                if paid:
                    receipt_day=receipt_at[:10]
                    receipt_boundary=datetime.fromisoformat(receipt_day).replace(tzinfo=timezone.utc)+timedelta(days=1)
                    boundaries[int(receipt_boundary.timestamp())]=receipt_day
                if limit < 1 or not 0 <= remaining <= limit or reset not in boundaries:
                    raise ValueError()
            except (KeyError, ValueError) as exc:
                # A confirmed 404 may omit quota headers. Its attempt was already
                # charged locally; never refund it or increase the saved allowance.
                if not missing or any(name in headers for name in quota_headers):
                    if status not in (200, 404, 429):
                        raise ValidationError("Equibles provider HTTP failure: " + str(status)) from exc
                    raise ValidationError("Equibles quota headers are missing or invalid") from exc
            else:
                if paid:
                    # A request remains charged on its reservation day. Headers
                    # describing the next day constrain that day, never refund it.
                    bucket=quota_bucket(state,boundaries[reset])
                    header_applied=apply_paid_headers(state,bucket,limit=limit,remaining=remaining,reset=reset,
                        receipt_at=receipt["captured_at"],reservation_at=pending["started_at"])
                else:
                    bucket["remaining"] = min(bucket["remaining"], remaining)
                    bucket["reset"] = reset
            if status == 429:
                if not paid or header_applied:
                    bucket["remaining"] = 0
                state["pending"] = None
                save()
                # Old-policy failures remain in their immutable attempt receipts;
                # they cannot restore an obsolete daily ceiling after conversion.
                raise Deferred("provider_quota" if not paid or header_applied else "quota_verification_required")
            if not missing and (status != 200 or "application/json" not in headers.get("content-type", "").lower()):
                # Keep the reservation until the outer failure handler saves the pause.
                # A crash before that save replays this durable error, never the GET.
                raise ValidationError("Equibles provider HTTP or content-type failure: " + str(status))
            atomic(root / "responses" / (catalogue_identifier(state,path) + ".json"), receipt)
            state["pending"] = None
            save()  # Quota and reservation clearance are committed together.
            return None if missing else body

        def settle_undispatched():
            pending=state["pending"]
            if pending is None:return False
            marker_path=root/"attempts"/(pending["attempt"]+".not-dispatched.json")
            if not marker_path.exists():return False
            marker=json.loads(read_file(marker_path,65536))
            if marker.get("reservation")!=pending or marker.get("outcome")!="not_dispatched":
                raise ConflictError("Undispatched transcript reservation differs")
            if response_receipt(root,pending["attempt"],pending["path"]) is not None:
                raise ConflictError("Undispatched transcript reservation has contradictory response evidence")
            if paid and pending.get("verification_reserved"):
                bucket=state["usage"][utc(pending["started_at"])[:10]]
                bucket["verification_requests"]-=1
                if bucket["verification_requests"]<0:
                    raise ConflictError("Undispatched quota verification count differs")
            state["pending"]=None
            state["undispatched_reservations"]=state.get("undispatched_reservations",0)+1
            save()
            return True

        def get(path):
            nonlocal requests, received
            settle_undispatched()
            found = retained(root, path, identifier=catalogue_identifier(state,path))
            if found is not None and epoch is not None and "/investor-events?" in path:
                if utc(found[0]["captured_at"]) < epoch["started_at"]:
                    raise ConflictError("Catalogue evidence predates its observation epoch")
            if state["pending"]:
                pending = state["pending"]
                if pending["path"] != path:
                    raise ConflictError("Equibles interrupted reservation scope differs")
                found = found or response_receipt(root, pending["attempt"], path)
                if found is None:
                    raise ConflictError("Equibles interrupted attempt needs reconciliation; no automatic retry")
                return accept_response(path, *found)
            if found:
                return None if found[0]["status"] == 404 else found[1]
            if cache_only:
                raise Deferred("cache_exhausted")
            if utc(clock().isoformat())[:10] != day or monotonic() - started >= run_seconds - 35:
                raise Deferred("day_or_runtime_limit")
            bucket = quota_bucket(state,day) if paid else state["usage"].setdefault(day, {"attempted": 0, "remaining": daily_cap})
            if bucket["attempted"] >= daily_cap or bucket["remaining"] <= 0:
                raise Deferred("daily_quota")
            if paid and not bucket["paid_headers_verified"] and bucket["verification_requests"]>=1:
                raise Deferred("quota_verification_required")
            if requests >= request_cap or received + MAX_BYTES > total_bytes:
                raise Deferred("run_resource_limit")
            if paid:
                previous=state.get("last_request_at")
                delay=0
                if previous is not None:
                    elapsed=(datetime.fromisoformat(utc(clock().isoformat()).replace("Z","+00:00"))
                        -datetime.fromisoformat(previous.replace("Z","+00:00"))).total_seconds()
                    if elapsed<0: raise ConflictError("Equibles request clock moved backwards")
                    delay=max(0,1-elapsed)
                if requests: delay=max(delay,1)
                if delay: sleeper(delay)
                if utc(clock().isoformat())[:10]!=day or monotonic()-started>=run_seconds-35:
                    raise Deferred("day_or_runtime_limit")
            elif requests:
                sleeper(1)
            identifier = day + "-" + str(bucket["attempted"] + 1).zfill(3)
            state["pending"] = {"path": path, "attempt": identifier, "started_at": utc(clock().isoformat())}
            if epoch is not None:
                state["pending"]["response_identifier"] = catalogue_identifier(state,path)
            bucket["attempted"] += 1
            bucket["remaining"] -= 1
            if paid:
                state["last_request_at"]=state["pending"]["started_at"]
                if not bucket["paid_headers_verified"]:
                    bucket["verification_requests"]+=1
                    state["pending"]["verification_reserved"]=True
            atomic(root / "attempts" / (identifier + ".json"), state["pending"])
            save()  # Reservation is durable before any provider request.
            if paid and monotonic()>=started+run_seconds-30:
                atomic(root/"attempts"/(identifier+".not-dispatched.json"),{
                    "outcome":"not_dispatched","reservation":dict(state["pending"]),
                    "deferred_at":utc(clock().isoformat())})
                settle_undispatched()
                raise Deferred("runtime_limit")
            requests += 1
            try:
                status, headers, body = transport.request(path)
            except Exception as exc:
                retain_transport_failure(root, state["pending"], exc, clock().isoformat())
                raise
            if not isinstance(body,bytes) or len(body)>MAX_BYTES:
                raise ResourceLimitError("Equibles response exceeds its byte bound")
            received += len(body)
            if received > total_bytes:
                raise ResourceLimitError("Equibles invocation exceeds its byte bound")
            headers = {k.lower(): str(v) for k,v in headers.items() if k.lower() in HEADERS}
            receipt = retain_response(root, path, body, clock().isoformat(), headers, status, identifier)
            return accept_response(path, receipt, body)

        outcome = "complete" if state["index"] == len(state["roster"]) else "running"
        if state["blocked"]:
            outcome = "blocked"
        else:
            try:
                while state["index"] < len(state["roster"]):
                    if monotonic() - started >= run_seconds - 35:
                        raise Deferred("runtime_limit")
                    row = state["roster"][state["index"]]
                    symbol = row["symbol"]
                    current = state["current"]
                    if current is None and symbol in state.get("deferred_progress",{}):
                        saved=state["deferred_progress"].pop(symbol)
                        if saved["instrument_id"]!=row["instrument_id"]:
                            raise ConflictError("Deferred transcript progress changed security identity")
                        current=state["current"]=saved["progress"]
                        save()
                    if current is None:
                        current = {"catalog": [], "catalog_done": False, "event_index": 0,
                                   "events": [], "pages": [], "offset": 0, "captured": 0}
                        state["current"] = current
                        save()
                    if not current["catalog_done"]:
                        path = f"/v1/stocks/{symbol}/investor-events?eventType=EarningsCall&limit=100&offset={len(current['catalog'])}"
                        unresolved = failure_policy.unresolved(state, path)
                        if unresolved is not None:
                            current["unresolved_catalogue"] = failure_policy.evidence(unresolved)
                            current["catalog_done"] = True
                            current["unavailable"] = "unresolved_catalogue_after_retry"
                            save()
                            continue
                        body = get(path)
                        if body is None:
                            if current["catalog"]:
                                raise ValidationError("Equibles catalogue vanished during pagination")
                            current["catalog_done"] = True
                            current["unavailable"] = "provider_ticker_not_found"
                        else:
                            rows, more = event_page(body, len(current["catalog"]))
                            all_rows = current["catalog"] + rows
                            if len(all_rows) > MAX_EVENTS or len({r["id"] for r in all_rows}) != len(all_rows):
                                raise ValidationError("Equibles catalogue repeats events or exceeds bound")
                            current["catalog"] = all_rows
                            if more:
                                if len(all_rows) >= MAX_EVENTS:
                                    raise ResourceLimitError("Equibles event pagination exceeds bound")
                                save()
                                continue
                            events = [r for r in all_rows if r["hasTranscript"]]
                            if len({(r["fiscalYear"], r["fiscalQuarter"]) for r in events}) != len(events):
                                raise ValidationError("Equibles fiscal call identity is ambiguous")
                            current["events"] = sorted(events, key=lambda r: (r["fiscalYear"], r["fiscalQuarter"]))
                            current["catalog_done"] = True
                        save()
                    if current["event_index"] == len(current["events"]):
                        gaps = current.get("unavailable_transcripts", [])
                        state["completed"][symbol] = {"transcripts": current["captured"],
                            "events": len(current["catalog"]),
                            "without_transcript": sum(not r["hasTranscript"] for r in current["catalog"]),
                            "unavailable_transcripts": gaps,
                            "status": current.get("unavailable",
                                "completed_with_transcript_gaps" if gaps else "complete_available_history"),
                            "finished_at": utc(clock().isoformat())}
                        if current.get("unresolved_transcripts"):
                            state["completed"][symbol]["unresolved_transcripts"] = current["unresolved_transcripts"]
                            state["completed"][symbol]["status"] = "completed_with_unresolved_transcripts"
                        if current.get("unresolved_catalogue"):
                            state["completed"][symbol]["unresolved_catalogue"] = current["unresolved_catalogue"]
                        state["index"] += 1
                        state["current"] = None
                        save()
                        continue
                    if len(current["pages"]) >= MAX_PAGES:
                        raise ResourceLimitError("Equibles transcript exceeds page bound before request")
                    event = current["events"][current["event_index"]]
                    known = None if epoch is None else epoch["known_calls"].get(call_key(symbol,event))
                    if known is not None:
                        if known != event["id"] or current["pages"]:
                            raise ConflictError("Catalogue changed a completed fiscal call identity")
                        current["event_index"] += 1
                        current["captured"] += 1
                        save()
                        continue
                    path = (f"/v1/stocks/{symbol}/earnings-calls/{event['fiscalYear']}/{event['fiscalQuarter']}"
                            f"/speakers?limit=200&offset={current['offset']}")
                    unresolved = failure_policy.unresolved(state, path)
                    if unresolved is not None:
                        current.setdefault("unresolved_transcripts", []).append({
                            "event_id": event["id"], "fiscal_year": event["fiscalYear"],
                            "fiscal_quarter": event["fiscalQuarter"],
                            **failure_policy.evidence(unresolved),
                            "partial_page_paths": list(current["pages"])})
                        current["event_index"] += 1
                        current["pages"], current["offset"] = [], 0
                        save()
                        continue
                    body = get(path)
                    empty = body is not None and empty_transcript_page(
                        body, symbol=symbol, event=event, offset=current["offset"])
                    if empty and current["pages"]:
                        raise ValidationError("Equibles empty transcript follows partial pages")
                    if body is None or empty:
                        receipt, _ = retained(root, path)
                        current.setdefault("unavailable_transcripts", []).append({
                            "event_id": event["id"], "fiscal_year": event["fiscalYear"],
                            "fiscal_quarter": event["fiscalQuarter"],
                            "reason": "provider_returned_empty_transcript" if empty else "provider_transcript_not_found",
                            "http_status": receipt["status"],
                            "path": path, "response_sha256": receipt["sha256"],
                            "observed_at": receipt["captured_at"],
                            "partial_page_paths": list(current["pages"])})
                        current["event_index"] += 1
                        current["pages"], current["offset"] = [], 0
                        save()  # Gap and next-quarter checkpoint advance atomically.
                        continue
                    value = transcript_page(body, symbol=symbol, event=event, offset=current["offset"])
                    paths = current["pages"] + [path]
                    if len(paths) > MAX_PAGES:
                        raise ResourceLimitError("Equibles transcript exceeds page bound")
                    if value["hasMore"]:
                        # Compare retained metadata and page contents before advancing a partial call.
                        prior = [json.loads(page_from(root, p).body) for p in current["pages"]]
                        if any(v["totalTurnCount"] != value["totalTurnCount"] or v["data"] == value["data"] for v in prior):
                            raise ValidationError("Equibles transcript pagination changed or repeated")
                        current["pages"] = paths
                        current["offset"] += value["turnCount"]
                        save()
                    else:
                        pages = tuple(page_from(root, p) for p in paths)
                        validate_bundle(symbol, row["instrument_id"], event, pages)
                        try:
                            if paid:
                                if monotonic()-started>=run_seconds:
                                    raise Deferred("runtime_limit")
                                publisher.publish(symbol=symbol, instrument_id=row["instrument_id"], event=event,
                                    pages=pages,deadline=started+run_seconds,monotonic=monotonic)
                            else:
                                publisher.publish(symbol=symbol, instrument_id=row["instrument_id"], event=event, pages=pages)
                        except PublicationDeferred:
                            if not paid or monotonic()-started<run_seconds:raise
                            raise Deferred("runtime_limit") from None
                        current["event_index"] += 1
                        current["captured"] += 1
                        current["pages"], current["offset"] = [], 0
                        state["transcripts"] += 1
                        if epoch is not None:
                            epoch["known_calls"][call_key(symbol,event)] = event["id"]
                        save()
                outcome = "complete"
            except Deferred as exc:
                outcome = str(exc)
            except Exception as exc:
                state["blocked"] = {"error": type(exc).__name__,
                    "reason": str(exc)[:256] if isinstance(exc, (ValidationError, ConflictError, ResourceLimitError)) else "local_publication_or_transport_failure",
                    "at": utc(clock().isoformat())}
                if isinstance(exc, EquiblesTransportFailure):
                    state["blocked"]["transport_failure"] = exc.diagnostic()
                save()
                outcome = "blocked"
        report = {"contract": "quant_data.equibles_transcript_backfill.v1", "outcome": outcome,
            "universe": len(state["roster"]), "completed_tickers": state["index"],
            "transcripts": state["transcripts"], "requests_this_run": requests,
            "response_bytes_this_run": received,
            "undispatched_reservations": state.get("undispatched_reservations",0),
            "skipped_quarters": sum(len(item.get("unavailable_transcripts", []))
                for item in state["completed"].values())
                + len((state["current"] or {}).get("unavailable_transcripts", [])),
            "quota": state["usage"].get(day),
            "current_ticker": state["roster"][state["index"]]["symbol"] if state["index"] < len(state["roster"]) else None,
            "blocked": state["blocked"]}
        if paid:
            selected_symbols={r["symbol"] for r in state["roster"]}
            report.update(contract="quant_data.equibles_transcript_backfill.v2",
                catalogue_epoch_id=None if epoch is None else epoch["id"],
                catalogue_observed_at=None if epoch is None else epoch["started_at"],
                selected_members=state["selection"]["selected_members"],
                resolved_subjects=len(state["roster"]),
                identity_gaps=len(state["selection"]["identity_gaps"]),
                membership_snapshot_id=state["selection"]["membership_snapshot_id"],
                mapping_id=state["selection"]["mapping_id"],scope_sha256=state["selection"]["scope_sha256"],
                quota_policy=state["quota_policy"]["bounds"],
                provider_paid_allowance_verified=state["quota_policy"]["provider_paid_allowance_verified"],
                selected_transcripts=sum(v["transcripts"] for k,v in state["completed"].items() if k in selected_symbols)
                    +(state["current"] or {}).get("captured",0)
                    +sum(saved["progress"].get("captured",0) for symbol,saved in state.get("deferred_progress",{}).items()
                        if symbol in selected_symbols),
                completed_outside_selection=sum(k not in selected_symbols for k in state["completed"]))
        if epoch is not None:
            report["selected_transcripts"] = sum(
                key.rsplit(":",2)[0] in selected_symbols for key in epoch["known_calls"])
        report.update(failure_policy.stats(state, day))
        if paid:
            from .equibles_catalogue import continuation_decision
            report["continuation"] = continuation_decision(report,observed_at=clock().isoformat())
        if not cache_only:
            atomic(root / "status.json", report, replace=True)
        return report


def main(argv=None):
    if tuple(sys.argv[1:] if argv is None else argv):
        print(dumps_strict({"error": "zero_argument_fixed_binding_required"}))
        return 64
    try:
        registry = load_registry(CANONICAL_REGISTRY_PATH, project_root=PROJECT_ROOT, environment={})
        publisher = EquiblesTranscriptPublisher(stores(), registry)
        key = read_project_credential(project_root=PROJECT_ROOT, name="EQUIBLES_API_KEY", environment=os.environ)
        from ..market.collection_bindings import load_bindings
        binding=load_bindings(PROJECT_ROOT/"config/collection_bindings.json")["equibles_transcripts"]
        # The existing source collector bounds one complete call bundle (at most
        # fifty pages). The paid job composes those bounded publications; its
        # per-invocation and daily budgets live in the versioned private policy.
        allow_paid=binding.mode=="active"
        # The -m entrypoint also exists as __main__. Use the imported module's
        # transport so its typed failures match the parallel worker's class.
        from . import equibles_transcript_backfill as runtime
        if allow_paid:
            from .equibles_daily_backfill import run_day
            report = run_day(STATE_ROOT, publisher, runtime.EquiblesTransport(key,start_method="spawn"),parallel=True)
        else:
            report = run(STATE_ROOT, publisher, runtime.EquiblesTransport(key),allow_paid=False)
        print(dumps_strict(report))
        return 75 if report["outcome"] == "blocked" else 0
    except Exception:
        print(dumps_strict({"error": "equibles_backfill_failed"}))
        return 75


if __name__ == "__main__":
    from .fetch_run_history import run_recorded_cli
    raise SystemExit(run_recorded_cli(
        "quant-data-equibles-transcripts.timer", main, argv=sys.argv[1:]
    ))
