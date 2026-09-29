"""Fixed daily selected-universe SF1 refresh over the bounded direct queue."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from dataclasses import replace
from pathlib import Path
import sys
import time

from ..errors import ConflictError, ResourceLimitError, ValidationError
from ..json_codec import loads_strict, dumps_strict
from ..stores import StoreMap, quiet_immutable_read_connection
from ..registry import load_registry
from ..market.collection_bindings import load_bindings, pin_binding
from ..market.collection_universe import _utc
from ..company.sharadar_direct import DIMENSIONS, parse_metadata
from .collection_targets import validate_selection
from .collection_sec import requires_active_binding
from .collection_plan import ProviderBudget, build_plan, validate_budget
from .collection_queue import atomic, _read, _response, _clock, _utcnow, run_queue
from .collection_sharadar_direct import metadata_unit, sf1_units, run_partition, run_definitions
from .collection_transport import host_fetch
from .sharadar_refresh_timing import SharadarRefreshTiming, SharadarClockRecoveryError
from .equibles_transcript_backfill import private_directory, job_lock

PROJECT_ROOT = Path("/home/volatility/Python_Projects/Quant_Data_Infra")
STATE_ROOT = PROJECT_ROOT / "data/.operations/collection/sharadar/direct"
BUDGET = ProviderBudget("sharadar", 500, 256 * 1024 * 1024, 3600, 1000, 1000)
CONTRACT = "quant_data.selected_sharadar_refresh.v2"


def initial_baseline(stores, selection, *, cutoff):
    """Prove all six full-history scopes, including explicitly empty responses."""
    validate_selection(stores, selection, cutoff=cutoff, collection="sharadar_fundamentals",
        require_active=requires_active_binding(stores))
    required = {(s.provider_symbol, s.provider_subject, d)
        for s in selection.eligible for d in DIMENSIONS}
    if not required:
        raise ConflictError("Sharadar refresh requires mapped selected subjects")
    found = {}
    with quiet_immutable_read_connection(stores, "company") as connection:
        rows = connection.execute("""SELECT i.source_ticker, i.provider_subject,
            c.scope_json, c.captured_at, c.transport_complete
            FROM company_sharadar_identity_assertions i
            JOIN company_sharadar_captures c ON c.capture_id=i.capture_id
            WHERE i.membership_snapshot_id=? AND i.mapping_id=? AND c.captured_at<=?
            ORDER BY c.captured_at LIMIT 200001""",
            (selection.membership_snapshot_id, selection.mapping_id, _utc(cutoff))).fetchall()
    if len(rows) > 200000:
        raise ResourceLimitError("Sharadar baseline proof exceeds its bound")
    for row in rows:
        scope = loads_strict(row["scope_json"])
        filters = scope.get("filters", {})
        legacy_history = (scope.get("channel") == 'nasdaq_data_link'
            and scope.get("table") == 'SHARADAR/SF1'
            and set(filters) == {"ticker", "dimension"})
        direct_history = (scope.get("channel") == "sharadar_direct"
            and scope.get("table") == "fundamentals"
            and set(filters) == {"ticker", "dimension", "format", "sort", "from", "to"}
            and filters["format"] == "json" and filters["sort"] == "date.asc"
            and filters["from"] == "1900-01-01"
            and filters["to"] == row["captured_at"][:10])
        if row["transport_complete"] != 1 or not (legacy_history or direct_history):
            continue
        dimension = filters["dimension"]
        if dimension not in DIMENSIONS:
            continue
        key = (row["source_ticker"], row["provider_subject"], dimension)
        if key in required:
            found.setdefault(key, row["captured_at"])
    if not required <= set(found):
        raise ConflictError("Sharadar full-history baseline is incomplete")
    # Start no later than the earliest response in the completed baseline.
    return min(found[key] for key in required)[:10]


def _binding(selection):
    return {"membership_snapshot_id": selection.membership_snapshot_id,
        "mapping_id": selection.mapping_id}


def _state(root, stores, selection, cutoff):
    path = root / "refresh-state.json"
    state = _read(path)
    if state is None:
        baseline = initial_baseline(stores, selection, cutoff=cutoff)
        state = {"contract": CONTRACT, **_binding(selection), "baseline_date": baseline,
            "last_complete_date": baseline, "last_complete_window": None, "pending": None}
        atomic(path, state)
    if (not isinstance(state, dict) or set(state) != {
        "contract", "membership_snapshot_id", "mapping_id", "baseline_date",
        "last_complete_date", "last_complete_window", "pending"}
        or state["contract"] != CONTRACT
        or any(state.get(key) != value for key, value in _binding(selection).items())):
        raise ConflictError("Sharadar refresh selection requires baseline reconciliation")
    for key in ("baseline_date", "last_complete_date"):
        value = state[key]
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValidationError("Sharadar refresh checkpoint date is invalid")
        if value > _utc(cutoff)[:10]:
            raise ConflictError("Sharadar refresh checkpoint is from the future")
    if state["last_complete_date"] < state["baseline_date"]:
        raise ConflictError("Sharadar refresh checkpoint moved before its baseline")
    if state["last_complete_window"] is not None:
        _utc(state["last_complete_window"])
    return state


def _run_refresh_pass(*, root, stores, registry, selection, fetch, budget=BUDGET,
        secret_values=(), utcnow=_utcnow, monotonic=time.monotonic, sleeper=time.sleep, recovery=None, deadline=None):
    started = monotonic()
    validate_budget(budget)
    if (budget.provider != "sharadar" or budget.max_requests > BUDGET.max_requests
        or budget.max_total_bytes > BUDGET.max_total_bytes
        or budget.max_run_seconds > BUDGET.max_run_seconds
        or budget.min_interval_milliseconds < 1000
        or budget.daily_ceiling != BUDGET.daily_ceiling):
        raise ResourceLimitError("Sharadar refresh exceeds the activated host bounds")
    root = Path(root)
    if not root.is_absolute() or root.resolve() != root:
        raise ValidationError("Sharadar refresh requires a resolved private root")
    deadline = min(started + budget.max_run_seconds, deadline) if deadline is not None else started + budget.max_run_seconds
    timing = SharadarRefreshTiming(utcnow, fetch, deadline=deadline,
        interval=budget.min_interval_milliseconds / 1000, monotonic=monotonic, sleeper=sleeper)
    utcnow, fetch = timing, timing.fetch
    private_directory(root)
    requests = received = completed = 0
    with job_lock(root / "refresh-controller"):
        timing.seed(root)
        validate_selection(stores, selection, cutoff=_clock(utcnow), collection="sharadar_fundamentals",
            require_active=requires_active_binding(stores))
        state = _state(root, stores, selection, _clock(utcnow))
        current_date = _clock(utcnow)[:10]
        current_window = current_date + "T00:00:00.000000Z"
        if state["pending"] is None and state["last_complete_window"] == current_window:
            return {"outcome": "already_complete", "requests": 0, "received_bytes": 0}
        if state["pending"] is None:
            start = (date.fromisoformat(state["last_complete_date"]) - timedelta(days=7)).isoformat()
            state["pending"] = {"window": current_window, "from_date": start, "to_date": current_date,
                "definitions_complete": False, "completed_units": []}
            atomic(root / "refresh-state.json", state, replace=True)
        pending = state["pending"]
        expected_start = (date.fromisoformat(state["last_complete_date"]) - timedelta(days=7)).isoformat()
        if (not isinstance(pending, dict) or set(pending) != {
            "window", "from_date", "to_date", "definitions_complete", "completed_units"}
            or pending["from_date"] != expected_start
            or _utc(pending["window"]) != pending["to_date"] + "T00:00:00.000000Z"
            or pending["to_date"] > current_date or pending["to_date"] < state["last_complete_date"]
            or type(pending["definitions_complete"]) is not bool
            or not isinstance(pending["completed_units"], list)
            or len(pending["completed_units"]) > 450
            or len(set(pending["completed_units"])) != len(pending["completed_units"])):
            raise ConflictError("Sharadar pending refresh window is invalid")

        total_partitions = 0

        def report(outcome):
            return {"outcome": outcome, "requests": requests, "received_bytes": received,
                "completed_this_run": completed, "window": pending["window"],
                "completed_partitions": len(pending["completed_units"]),
                "identity_gaps": len(selection.gaps), "total_partitions": total_partitions}
        def remaining(request_cap, byte_cap, second_cap):
            seconds = int(deadline - monotonic())
            req = min(request_cap, budget.max_requests - requests)
            size = min(byte_cap, budget.max_total_bytes - received)
            if seconds <= 30 or req < 1 or size < 16 * 1024 * 1024:
                return None
            return ProviderBudget("sharadar", req, size, min(second_cap, seconds),
                budget.min_interval_milliseconds, budget.daily_ceiling)
        def account(result):
            nonlocal requests, received
            requests += result.get("requests", 0)
            received += result.get("received_bytes", 0)

        if not pending["definitions_complete"]:
            allowance = remaining(1, 16 * 1024 * 1024, 30)
            if allowance is None:
                return report("invocation_budget")
            with timing.bound(monotonic() + allowance.max_run_seconds):
                result = run_definitions(root=root, stores=stores, registry=registry, selection=selection,
                    mode="incremental", observation_window=pending["window"], budget=allowance, fetch=fetch,
                    secret_values=secret_values, utcnow=utcnow, monotonic=monotonic, sleeper=sleeper,
                    deadline=timing.deadline, before_request=timing.before_request, recovery=recovery)
            account(result)
            if result["outcome"] not in ("succeeded", "unchanged"):
                return report(result["outcome"])
            pending["definitions_complete"] = True
            atomic(root / "refresh-state.json", state, replace=True)

        meta = metadata_unit(selection, mode="incremental", observation_window=pending["window"])
        evidence = recovery.response(meta) if recovery else _response(root, meta.unit_id, meta)
        if evidence is None:
            allowance = remaining(1, 16 * 1024 * 1024, 3600)
            if allowance is None:
                return report("invocation_budget")
            plan = build_plan(selections=(selection,), units=(recovery.unit(meta) if recovery else meta,), budgets=(allowance,), created_at=_clock(utcnow))
            if not timing.before_request(unit=meta, deadline=deadline):
                return report("invocation_budget")
            result = run_queue(root=root, plan=plan, provider="sharadar", fetch=fetch, publish=None,
                acquire_only=True, secret_values=secret_values, utcnow=utcnow, monotonic=monotonic, sleeper=sleeper,
                deadline=deadline)
            account(result)
            if result["outcome"] != "acquired":
                return report(result["outcome"])
            evidence = recovery.response(meta) if recovery else _response(root, meta.unit_id, meta)
        receipt, body = evidence
        if receipt["status"] != 200:
            return report("metadata_http_failure")
        schema = parse_metadata(body, captured_at=receipt["captured_at"],
            source_reference="collection/sharadar/blobs/" + receipt["content_sha256"] + ".json")
        units = sf1_units(stores, selection, schema=schema, mode="incremental",
            observation_window=pending["window"], cutoff=_clock(utcnow), date_to=pending["to_date"], batch_size=100,
            lastupdated=(pending["from_date"], pending["to_date"]))
        total_partitions = len(units)
        if not set(pending["completed_units"]) <= {unit.unit_id for unit in units}:
            raise ConflictError("Sharadar completed partitions differ from the pending manifest")
        for unit in units:
            if unit.unit_id in pending["completed_units"]:
                continue
            allowance = remaining(100, 256 * 1024 * 1024, 3600)
            if allowance is None:
                return report("invocation_budget")
            with timing.bound(monotonic() + allowance.max_run_seconds):
                result = run_partition(root=root, stores=stores, registry=registry, selection=selection,
                    initial_unit=unit, budget=allowance, fetch=fetch, secret_values=secret_values,
                    utcnow=utcnow, monotonic=monotonic, sleeper=sleeper,
                    deadline=timing.deadline, before_request=timing.before_request, recovery=recovery)
            account(result)
            if result["outcome"] not in ("succeeded", "unchanged"):
                return report(result["outcome"])
            pending["completed_units"].append(unit.unit_id)
            completed += 1
            atomic(root / "refresh-state.json", state, replace=True)
        if monotonic() >= deadline:
            return report("invocation_budget")
        result = report("succeeded")
        state["last_complete_date"] = pending["to_date"]
        state["last_complete_window"] = pending["window"]
        state["pending"] = None
        atomic(root / "refresh-state.json", state, replace=True)
        if recovery is None:
            private_directory(root / "refresh-completions")
            atomic(root / "refresh-completions" / (pending["to_date"] + ".json"), result)
        return result



def run_refresh(*, root, stores, registry, selection, fetch, budget=BUDGET,
        secret_values=(), utcnow=_utcnow, monotonic=time.monotonic, sleeper=time.sleep):
    from .sharadar_recovery import SharadarRecovery
    validate_budget(budget)
    if (budget.provider != "sharadar" or budget.max_requests > BUDGET.max_requests
            or budget.max_total_bytes > BUDGET.max_total_bytes
            or budget.max_run_seconds > BUDGET.max_run_seconds
            or budget.min_interval_milliseconds < 1000 or budget.daily_ceiling != BUDGET.daily_ceiling):
        raise ResourceLimitError("Sharadar refresh exceeds the activated host bounds")
    root = Path(root)
    if not root.is_absolute() or root.resolve() != root:
        raise ValidationError("Sharadar refresh requires a resolved private root")
    deadline = monotonic() + budget.max_run_seconds
    requests = received = completed = 0
    result = {"outcome": "invocation_budget"}
    private_directory(root)
    with job_lock(root / "refresh-supervisor"):
        recovery = SharadarRecovery(root, utcnow=utcnow, monotonic=monotonic,
            sleeper=sleeper, deadline=deadline)
        while True:
            seconds = int(deadline - monotonic())
            if (seconds <= 30 or requests >= budget.max_requests
                    or budget.max_total_bytes - received < 16 * 1024 * 1024):
                result["outcome"] = "invocation_budget"
                break
            allowance = replace(budget, max_requests=budget.max_requests - requests,
                max_total_bytes=budget.max_total_bytes - received, max_run_seconds=seconds)
            recovery.last_failure = None
            result = _run_refresh_pass(root=root, stores=stores, registry=registry,
                selection=selection, fetch=fetch, budget=allowance, secret_values=secret_values,
                utcnow=utcnow, monotonic=monotonic, sleeper=sleeper, recovery=recovery, deadline=deadline)
            requests += result.get("requests", 0)
            received += result.get("received_bytes", 0)
            completed += result.get("completed_this_run", 0)
            if recovery.last_failure is None:
                break
            if requests >= budget.max_requests or budget.max_total_bytes - received < 16 * 1024 * 1024:
                result["outcome"] = "invocation_budget"
                break
            action = recovery.retry()
            if action != "retry":
                if action != "not_retryable":
                    result["outcome"] = action
                break
        result.update(requests=requests, received_bytes=received, completed_this_run=completed)
        result["details"] = recovery.details(result)
        if result["outcome"] == "succeeded":
            private_directory(root / "refresh-completions")
            atomic(root / "refresh-completions" / (result["window"][:10] + ".json"), result)
        return result


def main(argv=None):
    if (sys.argv[1:] if argv is None else argv):
        print(dumps_strict({"outcome": "invalid_arguments"}))
        return 64
    try:
        stores = StoreMap.four_explicit(**{r: PROJECT_ROOT / "data" / (r + ".sqlite")
            for r in ("market", "macro", "company", "news")})
        registry = load_registry(PROJECT_ROOT / "config/system_registry.json",
            project_root=PROJECT_ROOT, environment={})
        selection = pin_binding(stores, load_bindings(PROJECT_ROOT / "config/collection_bindings.json")["sharadar_fundamentals"])
        if selection.binding.mode != "active":
            raise ConflictError("Sharadar selected binding is not active")
        fetch = host_fetch(PROJECT_ROOT, "sharadar")
        result = run_refresh(root=STATE_ROOT, stores=stores, registry=registry, selection=selection,
            fetch=fetch, secret_values=fetch.secret_values)
        from .fetch_run_summary import record_report
        from .sharadar_run_details import capture_details
        record_report("quant-data-sharadar-selected-refresh.timer", result)
        if "details" in result:
            capture_details(result["details"])
        print(dumps_strict(result))
        return 0 if result["outcome"] in ("succeeded", "already_complete") else 75
    except SharadarClockRecoveryError:
        print(dumps_strict({"outcome": "blocked", "error": "SharadarClockRecoveryError",
            "reason": "clock_recovery_exhausted"}))
        return 75
    except (ConflictError, ResourceLimitError, ValidationError) as error:
        print(dumps_strict({"outcome": "blocked", "error": type(error).__name__}))
        return 75


if __name__ == "__main__":
    from .fetch_run_history import run_recorded_cli
    raise SystemExit(run_recorded_cli(
        "quant-data-sharadar-selected-refresh.timer", main, argv=sys.argv[1:]
    ))
