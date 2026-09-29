"""Scheduled SEC entry point; legacy paired worker retained for explicit compatibility."""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import sys
import time

from ..errors import ConflictError, ResourceLimitError, ValidationError
from ..json_codec import dumps_strict
from ..stores import StoreMap
from ..registry import load_registry
from ..market.collection_bindings import load_bindings, pin_binding
from .collection_targets import validate_selection
from .collection_sec import sec_units, requires_active_binding, publish_sec_pairs, SecSourceRejected
from .collection_plan import ProviderBudget, build_plan
from .collection_queue import atomic, _read, _clock, _utcnow, run_queue
from .collection_transport import host_fetch
from .equibles_transcript_backfill import private_directory, job_lock
from .fetch_run_summary import record_report

PROJECT_ROOT = Path("/home/volatility/Python_Projects/Quant_Data_Infra")
STATE_ROOT = PROJECT_ROOT / "data/.operations/collection/sec"
MAX_REQUESTS = 4500
MAX_BYTES = 12 * 1024 * 1024 * 1024
MAX_SECONDS = 6 * 3600
CONTRACT = "quant_data.selected_sec_refresh.v1"


def run_refresh(*, root, stores, registry, selection, fetch, secret_values=(),
        request_limit=MAX_REQUESTS, byte_limit=MAX_BYTES, second_limit=MAX_SECONDS,
        utcnow=_utcnow, monotonic=time.monotonic, sleeper=time.sleep):
    started = monotonic()
    if any(type(value) is not int or not 1 <= value <= maximum for value, maximum in (
        (request_limit, MAX_REQUESTS), (byte_limit, MAX_BYTES), (second_limit, MAX_SECONDS))):
        raise ResourceLimitError("SEC selected refresh exceeds its activated bounds")
    root = Path(root)
    if not root.is_absolute() or root.resolve() != root:
        raise ValidationError("SEC selected refresh requires its resolved private root")
    at = _clock(utcnow)
    validate_selection(stores, selection, cutoff=at, collection="sec_filings_companyfacts",
        require_active=requires_active_binding(stores))
    window = at[:10] + "T00:00:00.000000Z"
    units = sec_units(stores, selection, mode="incremental", observation_window=window,
        cutoff=at, require_active=requires_active_binding(stores))
    if len(units) > MAX_REQUESTS:
        raise ResourceLimitError("SEC selected issuer count exceeds the approved run")
    # Each scheduled date is one new current observation. Earlier incomplete
    # dates retain their original receipts; they are never silently relabelled.
    queue = root / "current" / at[:10]
    private_directory(root)
    with job_lock(root / "current-controller"):
        private_directory(queue)
        previous = _read(queue / "scheduled-result.json")
        if previous is not None:
            if (previous.get("contract") != CONTRACT
                or previous.get("selection_sha256") != selection.scope_sha256):
                raise ConflictError("SEC completed date differs from the pinned selection")
            return {**previous, "outcome": "already_recorded", "requests_this_run": 0}
        plan_manifest = {"contract": CONTRACT, "observation_window": window,
            "membership_snapshot_id": selection.membership_snapshot_id,
            "mapping_id": selection.mapping_id, "selection_sha256": selection.scope_sha256,
            "max_requests": request_limit, "max_total_bytes": byte_limit,
            "max_run_seconds": second_limit, "retries": 0,
            "unit_ids": [unit.unit_id for unit in units]}
        old_manifest = _read(queue / "scheduled-manifest.json")
        if old_manifest is not None and old_manifest != plan_manifest:
            raise ConflictError("SEC scheduled manifest changed during the date")
        atomic(queue / "scheduled-manifest.json", plan_manifest)
        requests = received = 0
        results = {}
        stop_reason = None
        for offset in range(0, len(units), 200):
            batch = units[offset:offset + 200]
            while True:
                remaining_seconds = int(second_limit - (monotonic() - started))
                remaining_bytes = byte_limit - received
                if (requests >= request_limit or remaining_seconds <= 120
                    or remaining_bytes < max(unit.max_response_bytes for unit in batch)):
                    stop_reason = "invocation_budget"
                    break
                budget = ProviderBudget("sec", min(200, request_limit - requests),
                    min(1024 * 1024 * 1024, remaining_bytes), min(7200, remaining_seconds), 1000)
                plan = build_plan(selections=(selection,), units=batch, budgets=(budget,),
                    created_at=_clock(utcnow))
                report = run_queue(root=queue, plan=plan, provider="sec", fetch=fetch,
                    publish=None, acquire_only=True, secret_values=secret_values,
                    utcnow=utcnow, monotonic=monotonic, sleeper=sleeper)
                requests += report.get("requests", 0)
                received += report.get("received_bytes", 0)
                # A source-local invalid issuer must not suppress independent
                # issuers. Store/lock failures and unexpected exceptions escape.
                for cik in sorted({unit.subject for unit in batch}):
                    if monotonic() - started >= second_limit:
                        stop_reason = "publication_deadline"
                        break
                    pair = tuple(unit for unit in batch if unit.subject == cik)
                    try:
                        receipts = publish_sec_pairs(root=queue, stores=stores, registry=registry,
                            selection=selection, units=pair, cutoff=_clock(utcnow),
                            deadline=started + second_limit, monotonic=monotonic,
                            hold_root=root / "sec-metadata-holds")
                    except SecSourceRejected as error:
                        results[cik] = "source_rejected:" + error.reason_code
                    else:
                        for receipt in receipts:
                            results[receipt["cik"]] = receipt["outcome"]
                            if receipt["outcome"] == "publication_deadline":
                                stop_reason = "publication_deadline"
                if stop_reason:
                    break
                if report["outcome"] in ("acquired", "acquired_with_gaps"):
                    break
                if report["outcome"] == "invocation_budget" and report.get("requests", 0) > 0:
                    # Only untouched units may dispatch; received pages and
                    # completed publications remain in the same exact queue.
                    continue
                stop_reason = report["outcome"]
                break
            if stop_reason:
                break
        all_ciks = {unit.subject for unit in units}
        successful = {cik for cik, outcome in results.items() if outcome in ("succeeded", "unchanged", "reused")}
        partial = {cik for cik, outcome in results.items() if outcome == "incomplete_pair"}
        failed = set(results) - successful - partial
        unattempted = all_ciks - set(results)
        result = {"contract": CONTRACT, "selection_sha256": selection.scope_sha256,
            "observation_window": window, "completed_at": _clock(utcnow),
            "outcome": "succeeded" if not (partial or failed or unattempted or stop_reason) else "partial",
            "stop_reason": stop_reason, "selected_cik_count": len(all_ciks),
            "succeeded_cik_count": len(successful), "failed_cik_count": len(failed),
            "partial_cik_count": len(partial), "unattempted_cik_count": len(unattempted),
            "failed_issuers": [{"cik": cik, "outcome": results[cik]} for cik in sorted(failed)],
            "skipped_existing_cik_count": 0, "identity_gap_count": len(selection.gaps),
            "requests": requests, "response_bytes": received, "retries": 0}
        # A bounded resource continuation can resume this date. A terminal
        # result is never run again under the same observation window.
        if stop_reason not in ("invocation_budget", "publication_deadline"):
            atomic(queue / "scheduled-result.json", result)
        atomic(queue / "latest-invocation.json", result, replace=True)
        return result


def main(argv=None):
    if tuple(sys.argv[1:] if argv is None else argv):
        print(dumps_strict({"outcome": "invalid_arguments"}))
        return 64
    try:
        stores = StoreMap.four_explicit(**{r: PROJECT_ROOT / "data" / (r + ".sqlite")
            for r in ("market", "macro", "company", "news")})
        registry = load_registry(PROJECT_ROOT / "config/system_registry.json",
            project_root=PROJECT_ROOT, environment={})
        selection = pin_binding(stores, load_bindings(PROJECT_ROOT / "config/collection_bindings.json")["sec_filings_companyfacts"])
        if selection.binding.mode != "active" or len(selection.subjects) != 2248:
            raise ConflictError("SEC selected host binding differs from the approved universe")
        fetch = host_fetch(PROJECT_ROOT, "sec")
        from .sec_conditional_refresh import run_conditional_refresh
        report = run_conditional_refresh(root=STATE_ROOT, stores=stores, registry=registry, selection=selection,
            fetch=fetch, secret_values=fetch.secret_values)
        record_report("quant-data-sec-company-fundamentals.timer", report)
        print(dumps_strict(report))
        return 75 if any(report.get(key, 0) for key in (
            "failed_cik_count", "partial_cik_count", "unattempted_cik_count")) else 0
    except (ConflictError, ResourceLimitError, ValidationError) as error:
        print(dumps_strict({"outcome": "blocked", "error": type(error).__name__}))
        return 75


if __name__ == "__main__":
    from .fetch_run_history import run_recorded_cli
    raise SystemExit(run_recorded_cli(
        "quant-data-sec-company-fundamentals.timer", main, argv=sys.argv[1:]
    ))
