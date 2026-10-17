"""One finite, resumable-without-retry population of selected company descriptions."""
from __future__ import annotations
import argparse
import json
import multiprocessing
import os
from pathlib import Path
import time
from dataclasses import asdict
from datetime import datetime
from ..company.short_descriptions import (
    MAX_BODY, DescriptionPublisher, digest, prepare_record, selected_inputs, utcnow)
from ..credentials import read_project_credential
from ..errors import ConflictError, ResourceLimitError, ValidationError
from ..json_codec import dumps_strict, loads_strict
from ..registry import CANONICAL_REGISTRY_PATH, load_registry
from ..stores import acquire_write_session, resolve_store_map
from .collection_queue import atomic, QueueResponse
from .collection_transport import RequestRoute, _http_once, _receive_until
from .collection_provider_policy import invoke_host_fmp
from .equibles_transcript_backfill import job_lock, private_directory, read_file

PROJECT_ROOT = Path("/home/volatility/Python_Projects/Quant_Data_Infra")
WORK_ROOT = PROJECT_ROOT / "data/.operations/company-short-descriptions/20260922"
REQUEST_CAP = 516
BYTE_CAP = 32 * 1024 * 1024
SECONDS_CAP = 1800

def prepare(stores, work_root, *, cutoff):
    work_root = Path(work_root)
    with acquire_write_session(stores, ("market",), timeout_seconds=5):
        members = selected_inputs(stores, cutoff=cutoff)
    missing = [m["source_symbol"] for m in members if m["source"] is None]
    if len(missing) > REQUEST_CAP:
        raise ResourceLimitError("Missing descriptions exceed the authorized 516-request cap")
    plan = {"contract": "company_short_description_population.v1", "created_at": cutoff,
            "max_requests": REQUEST_CAP, "max_bytes": BYTE_CAP, "max_seconds": SECONDS_CAP,
            "members": members, "missing_symbols": missing}
    plan["plan_id"] = digest(plan)
    private_directory(work_root)
    atomic(work_root / "plan.json", plan)
    return {"plan_id": plan["plan_id"], "selected": len(members), "reuse": len(members)-len(missing),
            "requests_planned": len(missing), "request_cap": REQUEST_CAP}

def load_plan(work_root):
    plan = loads_strict(read_file(Path(work_root) / "plan.json", 16*1024*1024), max_bytes=16*1024*1024)
    material = {k:v for k,v in plan.items() if k != "plan_id"}
    if plan["plan_id"] != digest(material) or plan["contract"] != "company_short_description_population.v1":
        raise ConflictError("Description execution plan differs")
    if (plan["max_requests"], plan["max_bytes"], plan["max_seconds"]) != (REQUEST_CAP, BYTE_CAP, SECONDS_CAP):
        raise ConflictError("Description execution bounds differ")
    if not 1 <= len(plan["members"]) <= 2248 or len({m["source_symbol"] for m in plan["members"]}) != len(plan["members"]):
        raise ConflictError("Description membership is invalid")
    missing = [m["source_symbol"] for m in plan["members"] if m["source"] is None]
    if missing != plan["missing_symbols"] or len(missing) > REQUEST_CAP:
        raise ConflictError("Description request manifest differs")
    return plan

def profile_fetch(credential, symbol, *, timeout_seconds=30):
    """Reuse the project's bounded HTTPS worker and account-wide allowance."""
    route = RequestRoute("financialmodelingprep.com", "/stable/profile",
                         (("symbol", symbol),), ("FMP_API_KEY",), "apikey")
    def request(remaining):
        ctx = multiprocessing.get_context("fork")
        receiver, sender = ctx.Pipe(duplex=False)
        child = ctx.Process(target=_http_once, args=(sender, route, credential, remaining, MAX_BODY, True))
        deadline = time.monotonic() + remaining
        child.start()
        sender.close()
        try:
            result = _receive_until(receiver, deadline, MAX_BODY)
            if not isinstance(result, tuple) or len(result) != 4:
                raise ResourceLimitError("Profile request did not return a bounded response")
            return QueueResponse(*result)
        finally:
            receiver.close()
            if child.is_alive():
                child.terminate()
            child.join(timeout=1)
            if child.is_alive():
                child.kill()
                child.join()
    return invoke_host_fmp(request, request_material={"operation": "short_descriptions",
        "endpoint": "/stable/profile", "symbol": symbol}, priority="backfill", timeout_seconds=timeout_seconds)

def acquire(work_root, fetch, *, clock=utcnow, monotonic=time.monotonic):
    """Each target gets at most one attempt; uncertain attempts remain blocked."""
    work_root = Path(work_root)
    plan = load_plan(work_root)
    attempts = 0
    outcomes = []
    start = monotonic()
    with job_lock(work_root):
        private_directory(work_root / "responses")
        start_path = work_root / "acquisition-start.json"
        if not start_path.exists():
            atomic(start_path, {"plan_id": plan["plan_id"], "started_at": clock()})
        execution = loads_strict(read_file(start_path, 4096), max_bytes=4096)
        if execution["plan_id"] != plan["plan_id"]:
            raise ConflictError("Description acquisition start differs")
        started_at = datetime.fromisoformat(execution["started_at"].replace("Z","+00:00"))
        if started_at.tzinfo is None:
            raise ConflictError("Description acquisition start is not timezone-aware")
        for member in plan["members"]:
            if member["source"] is not None:
                continue
            symbol = member["source_symbol"]
            response_path = work_root / "responses" / (symbol + ".json")
            attempt_path = work_root / "responses" / (symbol + ".attempt.json")
            if response_path.exists():
                saved = loads_strict(read_file(response_path, 2*MAX_BODY), max_bytes=2*MAX_BODY)
                if saved["plan_id"] != plan["plan_id"] or saved["symbol"] != symbol or digest(saved["body"].encode()) != saved["body_sha256"]:
                    raise ConflictError("Retained profile response differs")
                outcomes.append({"symbol": symbol, "status": saved["status"], "reused_response": True})
                if saved["status"] in (401,403,429) or saved["status"] >= 500:
                    break
                continue
            if attempt_path.exists():
                raise ConflictError("Profile attempt is uncertain or failed; no retry is authorized")
            # Count all earlier charges and bytes, including an earlier invocation.
            charges = list((work_root / "responses").glob("*.attempt.json"))
            retained_bytes = 0
            for old_member in plan["missing_symbols"]:
                old_path = work_root / "responses" / (old_member + ".json")
                if old_path.exists():
                    saved = loads_strict(read_file(old_path, 2*MAX_BODY), max_bytes=2*MAX_BODY)
                    retained_bytes += len(saved["body"].encode())
            elapsed = (datetime.fromisoformat(clock().replace("Z","+00:00")) - started_at).total_seconds()
            if elapsed < 0:
                raise ConflictError("Description acquisition clock moved backward")
            remaining = int(min(SECONDS_CAP - (monotonic()-start), SECONDS_CAP-elapsed))
            if len(charges) >= REQUEST_CAP or remaining < 1 or retained_bytes + MAX_BODY > BYTE_CAP:
                raise ResourceLimitError("Profile population request, duration, or byte cap reached")
            atomic(attempt_path, {"plan_id": plan["plan_id"], "symbol": symbol,
                "provider_symbol": member["provider_symbol"], "attempted_at": clock()})
            attempts += 1
            response = fetch(member["provider_symbol"], timeout_seconds=min(30,remaining))
            raw = response.body
            if not isinstance(raw, bytes) or len(raw) > MAX_BODY:
                raise ResourceLimitError("Profile response exceeds byte bound")
            saved = {"plan_id": plan["plan_id"], "symbol": symbol, "status": response.status,
                     "captured_at": response.captured_at, "body": raw.decode("utf-8"),
                     "body_sha256": digest(raw)}
            atomic(response_path, saved)
            outcomes.append({"symbol": symbol, "status": response.status, "reused_response": False})
            if attempts % 25 == 0:
                print(dumps_strict({"event": "profile_progress", "attempted": attempts,
                                   "planned": len(plan["missing_symbols"])}), flush=True)
            if response.status in (401,403,429) or response.status >= 500:
                break
    return {"attempted_this_invocation": attempts, "retained_responses": len(outcomes),
            "http_status_counts": {str(s):sum(r["status"] == s for r in outcomes) for s in sorted({r["status"] for r in outcomes})}}

def prepared_records(work_root):
    work_root = Path(work_root)
    plan = load_plan(work_root)
    records, gaps = [], []
    for member in plan["members"]:
        source = member["source"]
        if source is None:
            path = work_root / "responses" / (member["source_symbol"] + ".json")
            if not path.exists():
                gaps.append({"symbol": member["source_symbol"], "reason": "profile_not_acquired"})
                continue
            saved = loads_strict(read_file(path, 2*MAX_BODY), max_bytes=2*MAX_BODY)
            if saved["plan_id"] != plan["plan_id"] or saved["symbol"] != member["source_symbol"] or digest(saved["body"].encode()) != saved["body_sha256"]:
                raise ConflictError("Retained response identity differs")
            if saved["status"] != 200:
                gaps.append({"symbol": member["source_symbol"], "reason": "http_"+str(saved["status"])})
                continue
            body = loads_strict(saved["body"], max_bytes=MAX_BODY)
            if not isinstance(body, list) or len(body) != 1:
                gaps.append({"symbol": member["source_symbol"], "reason": "profile_not_single_row"})
                continue
            source = {"raw_body": saved["body"], "captured_at": saved["captured_at"],
                      "source_pointer": "/0", "source_reference": "company-short-descriptions/20260922/responses/"+member["source_symbol"]+".json",
                      "origin": "fmp_profile", "source_evidence_id": None}
        try:
            records.append(prepare_record(member, source))
        except (ConflictError, ValidationError, ResourceLimitError) as exc:
            gaps.append({"symbol": member["source_symbol"], "reason": str(exc)})
    return records, gaps

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "acquire", "inspect", "publish"))
    args = parser.parse_args(argv)
    registry = load_registry(CANONICAL_REGISTRY_PATH, project_root=PROJECT_ROOT, environment={})
    stores = resolve_store_map(registry, project_root=PROJECT_ROOT, environment={})
    if args.action == "prepare":
        result = prepare(stores, WORK_ROOT, cutoff=utcnow())
    elif args.action == "acquire":
        # Read and validate the frozen workload before resolving a credential.
        load_plan(WORK_ROOT)
        key = read_project_credential(project_root=PROJECT_ROOT, name="FMP_API_KEY", environment=os.environ)
        result = acquire(WORK_ROOT, lambda symbol, **kw: profile_fetch(key, symbol, **kw))
    else:
        records, gaps = prepared_records(WORK_ROOT)
        result = {"prepared": len(records), "gaps": gaps}
        if args.action == "publish":
            if gaps:
                raise ConflictError("Description population has unresolved gaps; inspect before publication")
            receipt = DescriptionPublisher(stores, registry).publish(records, published_at=utcnow())
            result["receipt"] = asdict(receipt)
            if receipt.outcome != "unchanged":
                atomic(WORK_ROOT / "publication.json", result)
        else:
            result["samples"] = [{"symbol":r["source_symbol"], "short_description":r["short_description"],
                                  "method":r["method"], "truncated":r["excerpt_truncated"]} for r in records[:10]]
    print(dumps_strict(result), flush=True)

if __name__ == "__main__":
    main()
