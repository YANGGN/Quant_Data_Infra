"""Saturday historical-price gap repair: one bounded attempt per completed week."""
import argparse
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo
import sys
import time as runtime_time
from ..errors import ConflictError, ValidationError
from ..json_codec import dumps_strict, loads_strict
from ..stores import StoreMap, quiet_immutable_read_connection
from ..market.collection_bindings import load_bindings, pin_binding
from ..market.stage12_incremental import Stage12BIncrementalCollector
from ..market.stage12_scope import load_stage12_market_v1_scope
from ..market.stage12b_scope import load_stage12b_incremental_market_v1_scope
from .collection_plan import ProviderBudget, build_plan
from .collection_price_windows import HistoryPriceWindow, history_price_units, SelectedPriceHistoryPublisher
from .collection_queue import atomic, _read, run_queue, BoundedForwardClock, provider_stop
from .collection_targets import selected_market_rows
from .price_fetch_policy import current_price_targets
from .collection_transport import host_fetch
from .equibles_transcript_backfill import private_directory, job_lock
from .selected_price_refresh import (
    ROOT, LIVE_ACTIVATION, _history_selection, require_history_completion,
    price_population_sha256,
)

TIMER = "quant-data-weekly-price-repair.timer"
STATE = Path("data/.operations/collection/daily-prices/weekly-repair")
MAX_REQUESTS = 3000
MAX_BYTES = 256 * 1024 * 1024
MAX_SECONDS = 3600
CALENDAR_VERSION = "4.13.1"
ZONE = ZoneInfo("America/Toronto")
INDEX_CALENDARS = {
    "^GSPC": "XNYS", "^DJI": "XNYS", "^NDX": "XNYS", "^IXIC": "XNYS",
    "^VIX": "XNYS", "^HSI": "XHKG", "^N225": "XTKS", "^GDAXI": "XETR",
    "^FCHI": "XPAR", "^KS11": "XKRX", "^FTSE": "XLON", "^TWII": "XTAI",
    "^AXJO": "XASX", "^GSPTSE": "XTSE",
}

def due_week(now):
    """Latest Saturday 02:00 slot, including delayed persistent activation."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValidationError("Weekly clock requires an aware timestamp")
    local = now.astimezone(ZONE)
    saturday = local.date() - timedelta(days=(local.weekday() - 5) % 7)
    slot = datetime.combine(saturday, time(2), ZONE)
    if local < slot:
        slot -= timedelta(days=7)
    end = slot.date() - timedelta(days=1)
    return slot, end - timedelta(days=4), end

@lru_cache(maxsize=128)
def exchange_sessions(calendar, start, end):
    import exchange_calendars as calendars
    if calendars.__version__ != CALENDAR_VERSION:
        raise ConflictError("Weekly repair calendar version needs review")
    return tuple(calendars.get_calendar(calendar).sessions_in_range(start, end).strftime("%Y-%m-%d"))

def audit_week(stores, rows, start, end, *, sessions=exchange_sessions):
    """Read indexed current prices only; never infer prices or IPO dates."""
    results = []
    with quiet_immutable_read_connection(stores, "market") as c:
        for row in rows:
            # Activation pins the existing US stock/ETF population. Unknown
            # index calendars remain gaps instead of assumed US sessions.
            calendar = INDEX_CALENDARS.get(row["provider_symbol"]) if row["asset_type"] == "index" else "XNYS"
            item = dict(row, calendar=calendar, missing=[], expected=[], issue=None)
            if calendar is None:
                item["issue"] = "unreviewed_exchange_calendar"
                results.append(item)
                continue
            try:
                expected = sessions(calendar, start, end)
            except Exception:
                item["issue"] = "calendar_unavailable"
                results.append(item)
                continue
            first = c.execute("SELECT first_trade_date FROM stage10_instruments WHERE instrument_id=?",
                              (row["instrument_id"],)).fetchone()[0]
            if first is None:
                observed = c.execute("""SELECT trade_date FROM stage10_daily_prices
                    WHERE instrument_id=? AND provider='fmp' AND price_variant='fmp_full_eod_v1'
                    AND currency_segment='provider_native' ORDER BY trade_date LIMIT 1""",
                    (row["instrument_id"],)).fetchone()
                first = observed[0] if observed else None
                if first is None or first > start:
                    item["issue"] = "listing_boundary_unverified"
            # Earliest retained date is only a conservative boundary, not an IPO claim.
            expected = tuple(d for d in expected if first is not None and d >= first)
            present = {v[0] for v in c.execute("""SELECT trade_date FROM stage10_daily_prices
                WHERE instrument_id=? AND trade_date BETWEEN ? AND ?
                AND provider='fmp' AND price_variant='fmp_full_eod_v1'
                AND currency_segment='provider_native'""", (row["instrument_id"], start, end))}
            item["expected"] = list(expected)
            item["missing"] = [d for d in expected if d not in present]
            results.append(item)
    return results

class EmptyWeeklyPriceResponse(ValidationError):
    """A retained empty array is an unresolved symbol, never a publication."""


def queue_usage(root):
    state = _read(root / "ledger.json")
    if state is None:
        return 0, 0
    return (sum(b["charged_attempts"] for b in state["usage"].values()),
            sum(b["received_bytes"] for b in state["usage"].values()))


class WeeklyPricePublisher(SelectedPriceHistoryPublisher):
    """Preserve original responses; skip existing prices in the write transaction."""
    def __init__(self, *, audit, **kwargs):
        super().__init__(**kwargs)
        self.audit = {r["provider_symbol"]: r for r in audit}
        for _, w in self.requests.values():
            if (date.fromisoformat(w.end) - date.fromisoformat(w.start)).days != 4:
                raise ValidationError("Weekly repair requires a Monday-Friday window")
            if date.fromisoformat(w.start).weekday() != 0 or not self.audit[w.symbol]["missing"]:
                raise ValidationError("Weekly repair requires evidenced missing sessions")
        self.collector._selected_price_missing_only = True

    def __call__(self, **kwargs):
        payload = loads_strict(kwargs["body"], max_bytes=65536)
        if payload == []:
            raise EmptyWeeklyPriceResponse("Provider returned no prices for the requested week")
        item = self.audit[kwargs["unit"].subject]
        if not isinstance(payload, list) or len(payload) > 5:
            raise ValidationError("Weekly response exceeds five sessions")
        dates = [r.get("date") for r in payload if isinstance(r, dict)]
        if (len(dates) != len(payload) or any(not isinstance(d,str) for d in dates)
                or len(set(dates)) != len(dates) or not set(item["missing"]) <= set(dates)
                or not set(dates) <= set(item["expected"])):
            raise ValidationError("Weekly response does not cover its missing exchange sessions")
        return super().__call__(**kwargs)

def repair_week(*, root, stores, selection, collector, fetch_factory, cutoff, start, end,
                monotonic=runtime_time.monotonic, utcnow=lambda: datetime.now(timezone.utc),
                audit_fn=audit_week, queue_root=None, authorized_symbols=None,
                max_new_requests=MAX_REQUESTS):
    private_directory(root)
    queue_root = root / "queue" if queue_root is None else queue_root
    if type(max_new_requests) is not int or not 0 <= max_new_requests <= MAX_REQUESTS:
        raise ValidationError("Weekly repair request cap is invalid")
    initial_requests, initial_bytes = queue_usage(queue_root)
    deadline = monotonic() + MAX_SECONDS
    clock = BoundedForwardClock(utcnow, deadline=deadline, monotonic=monotonic)
    rows = selected_market_rows(stores, selection, cutoff=cutoff)
    rows, excluded = current_price_targets(rows, session=end)
    before = audit_fn(stores, rows, start, end)
    atomic(root / "before.json", before)
    windows = tuple(HistoryPriceWindow(r["provider_symbol"], start, end) for r in before
                    if r["missing"] and (authorized_symbols is None or r["provider_symbol"] in authorized_symbols))
    requests = history_price_units(stores, selection, windows=windows, cutoff=cutoff)[:MAX_REQUESTS]
    publisher = WeeklyPricePublisher(stores=stores, selection=selection, cutoff=cutoff,
        collector=collector, requests=requests, audit=before) if requests else None
    totals = dict(requests=0, received_bytes=0, published=0, reused=0)
    reports, failure, transport = [], None, None
    symbol_outcomes = []

    def fetch(**kwargs):
        # Replaying a retained response does not need credentials.
        nonlocal transport
        if transport is None:
            transport = fetch_factory()
        response = transport(**kwargs)
        # The queue validates/redacts the same secrets after this call.
        return response

    # One unit per queue call isolates only the known empty-response outcome.
    # Shared queue state retains rate limits, provider stops and uncertain-attempt guards.
    for original, _ in requests:
        used, received = queue_usage(queue_root)
        totals["requests"], totals["received_bytes"] = used - initial_requests, received - initial_bytes
        remaining = int(deadline - monotonic())
        cached = (queue_root / "responses" / (original.unit_id + ".json")).exists()
        if (remaining < 45 or MAX_BYTES - totals["received_bytes"] < 65536
                or (not cached and totals["requests"] >= max_new_requests)):
            failure = "invocation_budget"
            break
        from dataclasses import replace
        unit = replace(original, max_response_bytes=65536, max_rows=5)
        plan = build_plan(selections=(selection,), units=(unit,),
            budgets=(ProviderBudget("fmp", 1, MAX_BYTES - totals["received_bytes"],
                                    remaining, 250),), created_at=cutoff)

        def publish(**kwargs):
            kwargs["unit"] = original
            return publisher(**kwargs, deadline=deadline, monotonic=monotonic)

        try:
            # Existing host transport exposes its redaction inputs at construction.
            # Only load it when the queue has no retained successful response.
            if not cached and transport is None:
                transport = fetch_factory()
            report = run_queue(root=queue_root, plan=plan, provider="fmp", fetch=fetch,
                publish=publish, secret_values=getattr(transport, "secret_values", ()),
                deadline=deadline, monotonic=monotonic, utcnow=clock)
        except EmptyWeeklyPriceResponse:
            receipt = _read(queue_root / "responses" / (unit.unit_id + ".json"))
            outcome = {"symbol": unit.subject, "unit_id": unit.unit_id,
                       "outcome": "empty_response", "missing_prices_preserved": True,
                       "content_sha256": receipt["content_sha256"],
                       "captured_at": receipt["captured_at"]}
            private_directory(root / "symbol-outcomes")
            atomic(root / "symbol-outcomes" / (unit.unit_id + ".json"), outcome)
            symbol_outcomes.append(outcome)
            continue
        except Exception as exc:
            failure = type(exc).__name__
            break
        reports.append(report)
        for key in ("published", "reused"):
            totals[key] += report.get(key, 0)
        used, received = queue_usage(queue_root)
        totals["requests"], totals["received_bytes"] = used - initial_requests, received - initial_bytes
        atomic(root / "progress.json", dict(totals, batches=reports, symbol_outcomes=symbol_outcomes), replace=True)
        if report["outcome"] not in ("complete", "complete_with_gaps"):
            failure = report["outcome"]
            break
    after = audit_fn(stores, rows, start, end)
    atomic(root / "after.json", after)
    unresolved = [r for r in after if r["missing"] or r["issue"]]
    result = {
        "contract": "quant_data.weekly_price_repair.v1",
        "outcome": "complete_with_gaps" if unresolved or failure or selection.gaps else "complete",
        "window_start": start, "window_end": end, "scope": selection.scope_sha256,
        "planned_requests": len(windows), "maximum_requests": max_new_requests,
        "maximum_bytes": MAX_BYTES, "maximum_seconds": MAX_SECONDS, "retries": 0,
        "missing_before": sum(len(r["missing"]) for r in before),
        "missing_after": sum(len(r["missing"]) for r in after),
        "identity_gaps": len(selection.gaps), "unresolved_symbols": len(unresolved),
        "calendar_version": CALENDAR_VERSION, "failure": failure, "batches": reports,
        "symbol_outcomes": symbol_outcomes, "excluded_symbols": list(excluded), **totals,
        "symbol_counts": {"unit": "symbols", "successful": len(rows) - len(unresolved),
                          "failed": len(unresolved), "partial": 0, "skipped": 0, "unattempted": 0},
    }
    used, received = queue_usage(queue_root)
    result["requests"], result["received_bytes"] = used - initial_requests, received - initial_bytes
    return result

def live_context(activation, cutoff):
    stores = StoreMap.four_explicit(**{r: ROOT / "data" / (r + ".sqlite")
                                     for r in ("market", "macro", "company", "news")})
    binding = load_bindings(ROOT / "config/collection_bindings.json")["daily_prices"]
    if binding.mode != "active":
        raise ConflictError("Daily-price binding is not active")
    selection = pin_binding(stores, binding, cutoff=cutoff)
    if activation["price_population_sha256"] != price_population_sha256(selection):
        # Reconstruct the original daily-price approval and prove that only
        # issuer metadata changed; it must also match the weekly calendar pin.
        from .equibles_transcript_backfill import read_file
        history_activation = loads_strict(
            read_file(ROOT / LIVE_ACTIVATION, 65536), max_bytes=65536)
        approved = _history_selection(
            selection, history_activation, stores=stores, cutoff=cutoff)
        if activation["price_population_sha256"] != price_population_sha256(approved):
            raise ConflictError("Weekly price population changed; calendar scope needs review")
    require_history_completion(ROOT, selection, stores=stores, cutoff=cutoff)
    selected_market_rows(stores, selection, cutoff=cutoff, require_active=True)
    collector = Stage12BIncrementalCollector._for_selected_market_prices(
        stores=stores, selection=selection, cutoff=cutoff,
        scope=load_stage12b_incremental_market_v1_scope(ROOT / "config/stage12b_incremental_market_v1_scope.json"),
        stage12a_scope=load_stage12_market_v1_scope(ROOT / "config/stage12_market_v1_scope.json"))
    return stores, selection, collector


def recover_week(week_end):
    """One explicit recovery, preserving the original run and reusing its queue."""
    try:
        end = date.fromisoformat(week_end)
    except (TypeError, ValueError):
        raise ValidationError("Recovery requires an exact Friday date") from None
    if end.isoformat() != week_end or end.weekday() != 4:
        raise ValidationError("Recovery requires an exact Friday date")
    base = ROOT / STATE
    with job_lock(base):
        original = base / week_end
        prior = _read(original / "result.json")
        started = _read(original / "started.json")
        before = _read(original / "before.json")
        if (not prior or not started or not isinstance(before, list)
                or prior.get("contract") != "quant_data.weekly_price_repair.v1"
                or prior.get("outcome") != "complete_with_gaps"
                or prior.get("window_end") != week_end
                or prior.get("window_start") != str(end - timedelta(days=4))
                or prior.get("scope") != started.get("scope")):
            raise ConflictError("Recovery requires the original completed weekly gap receipts")
        recovery = original / "manual-recovery"
        completed = _read(recovery / "result.json")
        if completed is not None:
            return completed
        if (recovery / "started.json").exists():
            raise ConflictError("Weekly recovery already attempted; manual reconciliation required")
        queue_root = original / "queue"
        state = _read(queue_root / "ledger.json")
        if state is not None and state.get("pending") is not None:
            raise ConflictError("An uncertain original request needs reconciliation; no retry")
        symbols = tuple(sorted(r["provider_symbol"] for r in before if r["missing"]))
        if not symbols or len(symbols) != len(set(symbols)) or len(symbols) > MAX_REQUESTS:
            raise ConflictError("Original repair symbol scope is invalid")
        recorded_requests, recorded_bytes = prior.get("requests"), prior.get("received_bytes")
        if (type(recorded_requests) is not int or not 0 <= recorded_requests <= len(symbols)
                or type(recorded_bytes) is not int or not 0 <= recorded_bytes <= MAX_BYTES
                or (recorded_requests and state is None)):
            raise ConflictError("Original request accounting is missing or invalid; no retry")
        charged, received = queue_usage(queue_root)
        if charged != recorded_requests or received != recorded_bytes:
            raise ConflictError("Original request accounting differs; no retry")
        if state is not None and any(
                isinstance(item.get("http_status"), int) and provider_stop(item["http_status"])
                for item in state["units"].values()):
            raise ConflictError("Original provider stop requires reconciliation; no retry")
        maximum = len(symbols) - charged
        cutoff = datetime.now(timezone.utc).isoformat()
        activation = _read(base / "activation.json")
        stores, selection, collector = live_context(activation, cutoff)
        private_directory(recovery)
        atomic(recovery / "started.json", {
            "at": cutoff, "original_week": week_end, "symbols": symbols,
            "maximum_new_requests": maximum, "retries": 0,
            "queue": str(queue_root), "original_requests_preserved": charged,
        })
        result = repair_week(root=recovery, stores=stores, selection=selection, collector=collector,
            fetch_factory=lambda: host_fetch(ROOT, "fmp", environment={}), cutoff=cutoff,
            start=prior["window_start"], end=week_end, queue_root=queue_root,
            authorized_symbols=symbols, max_new_requests=maximum)
        result["recovery_of"] = week_end
        atomic(recovery / "result.json", result)
        return result


def run_live():
    now = datetime.now(timezone.utc)
    slot, start, end = due_week(now)
    base = ROOT / STATE
    activation = _read(base / "activation.json")
    first_slot = datetime.fromisoformat(activation["first_slot"])
    if slot < first_slot:
        return {"outcome": "not_due", "requests": 0}
    private_directory(base)
    with job_lock(base):
        root = base / end.isoformat()
        private_directory(root)
        if (root / "result.json").exists():
            return _read(root / "result.json")
        if (root / "started.json").exists():
            raise ConflictError("Weekly repair already attempted; manual reconciliation required")
        cutoff = now.isoformat()
        stores, selection, collector = live_context(activation, cutoff)
        missed = []
        prior = first_slot.date() - timedelta(days=1)
        while prior < end:
            previous = _read(base / prior.isoformat() / "result.json")
            if previous is None or previous.get("outcome") != "complete":
                missed.append(prior.isoformat())
            prior += timedelta(days=7)
        atomic(root / "started.json", {"at": cutoff, "slot": slot.isoformat(),
            "scope": selection.scope_sha256, "missed_older_weeks": missed,
            "maximum_requests": MAX_REQUESTS, "maximum_bytes": MAX_BYTES,
            "maximum_seconds": MAX_SECONDS, "retries": 0})
        result = repair_week(root=root, stores=stores, selection=selection, collector=collector,
            fetch_factory=lambda: host_fetch(ROOT, "fmp", environment={}),
            cutoff=cutoff, start=start.isoformat(), end=end.isoformat())
        result["missed_older_weeks"] = missed
        if missed:
            result["outcome"] = "complete_with_gaps"
        atomic(root / "result.json", result)
        return result

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recover-week", metavar="YYYY-MM-DD",
                        help="Explicit one-time recovery of an existing Friday receipt")
    args = parser.parse_args(argv or [])
    try:
        result = recover_week(args.recover_week) if args.recover_week else run_live()
        from .fetch_run_summary import record_report
        record_report(TIMER, result)
        print(dumps_strict(result))
        return 0 if result["outcome"] in ("complete", "not_due") else 1
    except Exception as exc:
        print(dumps_strict({"outcome": "failed", "error_type": type(exc).__name__}), file=sys.stderr)
        return 1

if __name__ == "__main__":
    from .fetch_run_history import run_recorded_cli
    operation = (lambda: main(sys.argv[1:])) if sys.argv[1:] else main
    raise SystemExit(run_recorded_cli("quant-data-weekly-price-repair.timer", operation, argv=sys.argv[1:]))
