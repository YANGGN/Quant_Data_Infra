"""Fixed, finite, ticker-by-ticker Equibles backfill with durable quota accounting."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import fcntl
import hashlib
import http.client
import json
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
HEADERS = frozenset(("content-type", "x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset"))


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


def retained(root, path):
    found = response_receipt(root, request_id(path), path)
    if found:
        status = found[0].get("status")
        if status != 200 and not (status == 404 and "/investor-events?" in path):
            raise ConflictError("Equibles cached response is not reusable")
    return found


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
    try:
        connection = http.client.HTTPSConnection("api.equibles.com", timeout=20)
        try:
            connection.request("GET", path, headers={"Authorization": "Bearer " + key,
                "Accept": "application/json", "User-Agent": "QuantDataInfra/1.0"})
            response = connection.getresponse()
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES or key.encode() in body:
                raise ValueError()
            headers = {k.lower(): v for k,v in response.getheaders() if k.lower() in HEADERS}
            if any(key in v for v in headers.values()):
                raise ValueError()
            sender.send((response.status, headers, body))
        finally:
            connection.close()
    except Exception:
        sender.send(None)
    finally:
        sender.close()


class EquiblesTransport:
    """One GET, no redirects/retries, and a hard 30-second process deadline."""
    def __init__(self, key):
        self.key = key

    def request(self, path):
        # Only the two fixed endpoint shapes produced by this module are permitted.
        import re
        if not re.fullmatch(r"/v1/stocks/[A-Z0-9][A-Z0-9.\-]{0,19}/(?:investor-events\?eventType=EarningsCall&limit=100&offset=\d{1,4}|earnings-calls/\d{4}/[1-4]/speakers\?limit=200&offset=\d{1,5})", path):
            raise ValidationError("Equibles request route is invalid")
        context = multiprocessing.get_context("fork")
        receiver, sender = context.Pipe(duplex=False)
        child = context.Process(target=_http_child, args=(sender, path, self.key))
        child.start()
        sender.close()
        try:
            if not receiver.poll(30):
                raise ResourceLimitError("Equibles request wall timeout")
            result = receiver.recv()
            if result is None:
                raise ResourceLimitError("Equibles transport failed")
            return result
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


def run(root, publisher, transport, *, clock=now, monotonic=time.monotonic, sleeper=time.sleep):
    """Internal explicit-root harness; the public executable accepts no paths or switches."""
    with job_lock(root):
        state = json.loads(read_file(root / "state.json", 32 * 1024 * 1024))
        if state.get("version") != 1 or len(state["roster"]) > MAX_TICKERS:
            raise ConflictError("Equibles checkpoint version or universe is invalid")
        started, day, requests, received = monotonic(), utc(clock().isoformat())[:10], 0, 0
        def save():
            atomic(root / "state.json", state, replace=True)
        def accept_response(path, receipt, body):
            # Also used on restart: a durable response is reconciled without another GET.
            pending = state["pending"]
            if not pending or pending["path"] != path:
                raise ConflictError("Equibles response has no matching reservation")
            reservation_day = utc(pending["started_at"])[:10]
            bucket = state["usage"][reservation_day]
            headers, status = receipt["headers"], receipt["status"]
            try:
                limit = int(headers["x-ratelimit-limit"])
                remaining = int(headers["x-ratelimit-remaining"])
                reset = int(headers["x-ratelimit-reset"])
                boundary = datetime.fromisoformat(reservation_day).replace(tzinfo=timezone.utc) + timedelta(days=1)
                if limit < 1 or not 0 <= remaining <= limit or reset != int(boundary.timestamp()):
                    raise ValueError()
            except (KeyError, ValueError) as exc:
                raise ValidationError("Equibles quota headers are missing or invalid") from exc
            bucket["remaining"] = min(bucket["remaining"], remaining)
            bucket["reset"] = reset
            if status == 429:
                bucket["remaining"] = 0
                state["pending"] = None
                save()
                raise Deferred("provider_quota")
            missing = status == 404 and "/investor-events?" in path
            if not missing and (status != 200 or "application/json" not in headers.get("content-type", "").lower()):
                # Keep the reservation until the outer failure handler saves the pause.
                # A crash before that save replays this durable error, never the GET.
                raise ValidationError("Equibles provider HTTP or content-type failure: " + str(status))
            atomic(root / "responses" / (request_id(path) + ".json"), receipt)
            state["pending"] = None
            save()  # Quota and reservation clearance are committed together.
            return None if missing else body

        def get(path):
            nonlocal requests, received
            found = retained(root, path)
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
            if utc(clock().isoformat())[:10] != day or monotonic() - started >= MAX_RUN_SECONDS - 35:
                raise Deferred("day_or_runtime_limit")
            bucket = state["usage"].setdefault(day, {"attempted": 0, "remaining": DAILY_CAP})
            if bucket["attempted"] >= DAILY_CAP or bucket["remaining"] <= 0:
                raise Deferred("daily_quota")
            if requests >= DAILY_CAP or received + MAX_BYTES > MAX_TOTAL_BYTES:
                raise Deferred("run_resource_limit")
            if requests:
                sleeper(1)
            identifier = day + "-" + str(bucket["attempted"] + 1).zfill(3)
            state["pending"] = {"path": path, "attempt": identifier, "started_at": utc(clock().isoformat())}
            bucket["attempted"] += 1
            bucket["remaining"] -= 1
            atomic(root / "attempts" / (identifier + ".json"), state["pending"])
            save()  # Reservation is durable before any provider request.
            requests += 1
            status, headers, body = transport.request(path)
            received += len(body)
            headers = {k.lower(): str(v) for k,v in headers.items() if k.lower() in HEADERS}
            receipt = retain_response(root, path, body, clock().isoformat(), headers, status, identifier)
            return accept_response(path, receipt, body)

        outcome = "complete" if state["index"] == len(state["roster"]) else "running"
        if state["blocked"]:
            outcome = "blocked"
        else:
            try:
                while state["index"] < len(state["roster"]):
                    if monotonic() - started >= MAX_RUN_SECONDS - 35:
                        raise Deferred("runtime_limit")
                    row = state["roster"][state["index"]]
                    symbol = row["symbol"]
                    current = state["current"]
                    if current is None:
                        current = {"catalog": [], "catalog_done": False, "event_index": 0,
                                   "events": [], "pages": [], "offset": 0, "captured": 0}
                        state["current"] = current
                        save()
                    if not current["catalog_done"]:
                        path = f"/v1/stocks/{symbol}/investor-events?eventType=EarningsCall&limit=100&offset={len(current['catalog'])}"
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
                        state["completed"][symbol] = {"transcripts": current["captured"],
                            "events": len(current["catalog"]),
                            "without_transcript": sum(not r["hasTranscript"] for r in current["catalog"]),
                            "status": current.get("unavailable", "complete_available_history"),
                            "finished_at": utc(clock().isoformat())}
                        state["index"] += 1
                        state["current"] = None
                        save()
                        continue
                    if len(current["pages"]) >= MAX_PAGES:
                        raise ResourceLimitError("Equibles transcript exceeds page bound before request")
                    event = current["events"][current["event_index"]]
                    path = (f"/v1/stocks/{symbol}/earnings-calls/{event['fiscalYear']}/{event['fiscalQuarter']}"
                            f"/speakers?limit=200&offset={current['offset']}")
                    body = get(path)
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
                        publisher.publish(symbol=symbol, instrument_id=row["instrument_id"], event=event, pages=pages)
                        current["event_index"] += 1
                        current["captured"] += 1
                        current["pages"], current["offset"] = [], 0
                        state["transcripts"] += 1
                        save()
                outcome = "complete"
            except Deferred as exc:
                outcome = str(exc)
            except Exception as exc:
                state["blocked"] = {"error": type(exc).__name__,
                    "reason": str(exc)[:256] if isinstance(exc, (ValidationError, ConflictError, ResourceLimitError)) else "local_publication_or_transport_failure",
                    "at": utc(clock().isoformat())}
                save()
                outcome = "blocked"
        report = {"contract": "quant_data.equibles_transcript_backfill.v1", "outcome": outcome,
            "universe": len(state["roster"]), "completed_tickers": state["index"],
            "transcripts": state["transcripts"], "requests_this_run": requests,
            "quota": state["usage"].get(day),
            "current_ticker": state["roster"][state["index"]]["symbol"] if state["index"] < len(state["roster"]) else None,
            "blocked": state["blocked"]}
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
        report = run(STATE_ROOT, publisher, EquiblesTransport(key))
        print(dumps_strict(report))
        return 75 if report["outcome"] == "blocked" else 0
    except Exception:
        print(dumps_strict({"error": "equibles_backfill_failed"}))
        return 75


if __name__ == "__main__":
    raise SystemExit(main())
