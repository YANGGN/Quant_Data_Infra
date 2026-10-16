"""Bounded daily Theta EOD collection and one missing-only repair per week.

The clock wrapper owns admission. Existing transport, extraction, physical locks,
canonical publication and verified source cleanup retain their historical rules.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import fcntl
import hashlib
import json
import os
import shutil
import time as runtime_time

from quant_data.stores import StoreWriteLock
from .job import SpyHistoryJob, atomic_json, sessions_from_calendars
from .model import build_capture, canonical_json
from .store import OptionsStore
from .transport import Budget, ProviderFailure, ThetaTransport
from .universe import OPTIONS_ETFS, TIER1_ETFS

NY = ZoneInfo("America/New_York")
STATE = Path("data/.operations/theta-options")
POLICY = {
    "contract": "theta_daily_and_weekly_v2",
    "symbols": list(TIER1_ETFS),
    "store": "data/options.sqlite",
    "history_floor": "2026-09-21",
    "daily": {"calendar": "Tue..Sat *-*-* 07:15:00 America/New_York",
              "max_requests": 32, "max_bytes": 2 * 1024**3, "max_seconds": 1200,
              "persistent": False},
    "weekly": {"calendar": "Sun *-*-* 03:00:00 America/New_York",
               "max_requests": 152, "max_bytes": 8 * 1024**3, "max_seconds": 3600,
               "persistent": True},
    "workers": 1, "retries": 0, "missing_only": True,
    "max_store_bytes": 25 * 1024**3, "min_free_bytes": 20 * 1024**3,
    "handler": "quant_data.operations.theta_options_refresh",
}
POLICY_SHA256 = hashlib.sha256(canonical_json(POLICY).encode()).hexdigest()
CROSS_ASSET_POLICY = {
    **POLICY,
    "contract": "theta_daily_and_weekly_v3",
    "symbols": list(OPTIONS_ETFS),
    "daily": {**POLICY["daily"], "max_requests": 56},
    "weekly": {**POLICY["weekly"], "max_requests": 272},
}
CROSS_ASSET_POLICY_SHA256 = hashlib.sha256(canonical_json(CROSS_ASSET_POLICY).encode()).hexdigest()
RECOVERABLE = {
    "UNAVAILABLE", "DEADLINE_EXCEEDED", "RESOURCE_EXHAUSTED",
    "INTERNAL", "ABORTED", "CANCELLED",
}


def due_window(mode, now):
    """Daily fetches the prior date; persistent weekly catches the latest due week."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("aware_clock_required")
    local = now.astimezone(NY)
    if mode == "daily":
        slot = datetime.combine(local.date(), time(7, 15), NY)
        if local.weekday() not in range(1, 6) or local < slot:
            return None
        session = local.date() - timedelta(days=1)
        return slot, session, session
    if mode != "weekly":
        raise ValueError("unknown_options_mode")
    sunday = local.date() - timedelta(days=(local.weekday() - 6) % 7)
    slot = datetime.combine(sunday, time(3), NY)
    if local < slot:
        slot -= timedelta(days=7)
    end = slot.date() - timedelta(days=2)
    return slot, end - timedelta(days=4), end


def read_coverage(store, sessions, symbols=TIER1_ETFS):
    """The physical lock guards a quiet immutable read; no write connection."""
    if not sessions:
        return set()
    with StoreWriteLock(store.path, timeout_seconds=10):
        with store.read() as connection:
            store._check(connection, require_latest=True)
            return {(row[0], row[1]) for row in connection.execute(
                "SELECT symbol,session_date FROM option_current "
                "WHERE session_date BETWEEN ? AND ?",
                (min(sessions), max(sessions)),
            ) if row[0] in symbols and row[1] in sessions}


def validate_calendars(calendars):
    # Provider calendars list exceptions; an empty stream cannot prove a year.
    for year, rows in calendars.items():
        if not rows:
            raise ValueError("calendar_empty")
        seen = set()
        for row in rows:
            day = date.fromisoformat(row["date"])
            if day.year != year or day in seen:
                raise ValueError("calendar_identity")
            seen.add(day)
            if row["type"] not in ("full_close", "early_close"):
                raise ValueError("calendar_status_unknown")


def _safe_error(exc):
    # Never expose provider/authentication text or source values in status.
    if isinstance(exc, ProviderFailure):
        return exc.code
    if type(exc) in (RuntimeError, ValueError) and str(exc).replace("_", "").isalnum():
        return str(exc)
    return type(exc).__name__


def period_namespace(mode, policy):
    if policy["contract"] == CROSS_ASSET_POLICY["contract"]:
        return mode + "-cross-asset"
    return "daily-next-day" if mode == "daily" else mode


class ScheduledOptionsRun(SpyHistoryJob):
    """Reuse only staging, receipts and cleanup; never invoke a backfill run."""
    STORE_LIMIT_BYTES = POLICY["max_store_bytes"]

    def __init__(self, root, mode, end, *, transport_factory=ThetaTransport, policy=POLICY):
        self.policy = policy
        self.policy_sha256 = hashlib.sha256(canonical_json(policy).encode()).hexdigest()
        self.symbols = tuple(policy["symbols"])
        self.WORK_DIRECTORY = str(STATE / period_namespace(mode, policy) / end.isoformat())
        super().__init__(root, transport_factory=transport_factory)
        self.mode = mode
        self.status = {
            "contract": policy["contract"], "mode": mode, "state": "starting",
            "policy_sha256": self.policy_sha256, "symbols": list(self.symbols),
            "automatic_retries": 0, "published": 0, "preserved": 0, "gaps": [],
        }

    def reconcile_cleanup(self):
        # The inherited immutable read is quiet under the shared physical lock.
        with StoreWriteLock(self.store.path, timeout_seconds=10):
            return super().reconcile_cleanup()

    def storage_check(self):
        if self.store.path.stat().st_size >= self.STORE_LIMIT_BYTES:
            raise RuntimeError("options_database_cap")
        if shutil.disk_usage(self.work).free < self.policy["min_free_bytes"]:
            raise RuntimeError("disk_reserve")

    def deadline_check(self):
        if runtime_time.monotonic() - self._budget.started >= self._budget.max_seconds:
            raise RuntimeError("duration_cap")

    def audit(self, pairs):
        present = read_coverage(self.store, {d for d, _ in pairs}, self.symbols)
        return [(s, d, p) for d, p in pairs for s in self.symbols if (s, d) not in present]

    def request_bounds(self):
        return self.policy[self.mode]

    def execute(self, start, end):
        bounds = self.request_bounds()
        self._budget = Budget(max_requests=bounds["max_requests"],
                              max_bytes=bounds["max_bytes"],
                              max_seconds=bounds["max_seconds"])
        transport = None
        self.status.update(window_start=start.isoformat(), window_end=end.isoformat(),
                           requests_cap=bounds["max_requests"], bytes_cap=bounds["max_bytes"],
                           seconds_cap=bounds["max_seconds"])
        try:
            self.storage_check()
            weekdays = set()
            day = start
            while day <= end:
                if day.weekday() < 5:
                    weekdays.add(day.isoformat())
                day += timedelta(days=1)
            present = read_coverage(self.store, weekdays, self.symbols)
            if all((s, d) in present for d in weekdays for s in self.symbols):
                self.status.update(state="complete", expected_sessions=None,
                                   reason="all_weekdays_already_present", unresolved=[])
                return self.status
            # Admission already exists before SDK authentication or any request.
            transport = self.transport_factory(self.root, self._budget,
                receipt_callback=self.receipt, symbols=self.symbols)
            self.status.update(state="planning", subscription_code=transport.subscription_code)
            calendars = {}
            for year in range((start - timedelta(days=14)).year, end.year + 1):
                batch, _ = self.cached_get(transport, "calendar_year", year=str(year))
                calendars[year] = batch["rows"]
            validate_calendars(calendars)
            pairs = sessions_from_calendars(calendars, start, end)
            missing = self.audit(pairs)
            atomic_json(self.work / "plan.json", {
                "policy_sha256": self.policy_sha256,
                "sessions": pairs, "missing": missing, "window_start": str(start),
                "window_end": str(end), "calendar_years": sorted(calendars),
            })
            self.status.update(state="running", expected_sessions=len(pairs) * len(self.symbols),
                               initially_missing=len(missing))
            self.persist()
            for symbol, session, previous in missing:
                self.deadline_check()
                self.storage_check()
                # Another authorized publisher may have filled a gap since the audit.
                if (symbol, session) in read_coverage(self.store, {session}, self.symbols):
                    self.status["preserved"] += 1
                    continue
                self.status.update(active_symbol=symbol, active_session=session)
                self.persist()
                day = date.fromisoformat(session)
                try:
                    greek, gpath = self.cached_get(transport, "option_history_greeks_eod",
                        symbol=symbol, expiration="*", start_date=day, end_date=day,
                        version="1", underlyer_use_nbbo=True)
                    if not greek["rows"]:
                        self.status["gaps"].append({"symbol": symbol, "session": session,
                                                    "reason": "eod_no_data"})
                        continue
                    oi, opath = self.cached_get(transport, "option_history_open_interest",
                                               symbol=symbol, expiration="*", date=day)
                    if not oi["rows"]:
                        self.status["gaps"].append({"symbol": symbol, "session": session,
                                                    "reason": "open_interest_no_data"})
                        continue
                    capture = build_capture(session, greek["rows"], oi["rows"],
                        [greek["receipt"], oi["receipt"]], previous_session=previous,
                        symbol=symbol)
                    if not capture["coverage"]["eod_contracts"] or not capture["coverage"]["oi_matched"]:
                        self.status["gaps"].append({"symbol": symbol, "session": session,
                                                    "reason": "no_usable_eod_or_oi"})
                        continue
                    self.deadline_check()
                    self.storage_check()
                    self.prepare_cleanup(capture, {gpath, opath})
                    result = self.store.publish(capture, missing_only=True)
                    if result["outcome"] == "existing":
                        # A race preserves existing data; unmatched staged evidence
                        # is retained, never mistaken for verified publication.
                        self.status["preserved"] += 1
                        (self.work / "pending-cleanup.json").unlink()
                    else:
                        if not self.reconcile_cleanup():
                            raise RuntimeError("cleanup_unverified_capture")
                        self.status["published"] += result["canonical_changes"]
                    self.status["last_capture_id"] = result["capture_id"]
                    del capture, greek, oi
                    self.persist()
                except ValueError as exc:
                    if str(exc) not in {"missing_contemporaneous_spot", "inconsistent_underlying_reference"}:
                        raise
                    self.status["gaps"].append({"symbol": symbol, "session": session,
                                                "reason": str(exc)})
                    self.persist()
                except ProviderFailure as exc:
                    if exc.code not in RECOVERABLE:
                        raise
                    self.status["gaps"].append({"symbol": symbol, "session": session,
                                                "reason": exc.code})
                    self.persist()
            self.deadline_check()
            unresolved = [{"symbol": s, "session": d} for s, d, p in self.audit(pairs)]
            self.status.update(state="complete_with_gaps" if unresolved else "complete",
                               unresolved=unresolved)
        except Exception as exc:
            self.status.update(state="stopped", error=_safe_error(exc))
            if isinstance(exc, ProviderFailure) and exc.receipt.get("provider_detail"):
                self.status["provider_detail"] = exc.receipt["provider_detail"]
        finally:
            if transport:
                transport.close()
            self.status.update(self._budget.snapshot(), finished_at=datetime.now(timezone.utc).isoformat())
            self.status.pop("active_symbol", None)
            self.status.pop("active_session", None)
            self.persist()
        return self.status


def sync_admission_directories(work, root):
    """Persist marker rename and newly created ancestors before authentication."""
    # atomic_json fsyncs the file; directories also need fsync for crash safety.
    # Stop at the explicit project root, never follow a directory symlink.
    directory = Path(work)
    root = Path(root)
    if not directory.is_relative_to(root):
        raise ValueError("options_admission_path")
    while True:
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        if directory == root:
            return
        directory = directory.parent


def run_scheduled(root, mode, *, now=None, transport_factory=ThetaTransport):
    root = Path(root).resolve(strict=True)
    now = now or datetime.now(timezone.utc)
    window = due_window(mode, now)
    if window is None:
        return {"state": "not_due", "data_requests": 0}
    slot, start, end = window
    store = OptionsStore(root)
    base = root / STATE
    if not base.resolve().is_relative_to(root):
        raise ValueError("options_state_path")
    cross_asset = (base / "activation-cross-asset.json").exists()
    policy = CROSS_ASSET_POLICY if cross_asset else POLICY
    policy_sha256 = CROSS_ASSET_POLICY_SHA256 if cross_asset else POLICY_SHA256
    registry_key = "scheduled_collection_cross_asset" if cross_asset else "scheduled_collection"
    if store.registry.get(registry_key) != policy:
        raise ValueError("options_scheduled_policy_drift")
    activation_name = "activation-cross-asset.json" if cross_asset else "activation-next-day.json"
    activation = json.loads((base / activation_name).read_text())
    if activation.get("policy_sha256") != policy_sha256:
        raise ValueError("options_activation_policy")
    first = datetime.fromisoformat(activation[mode + "_first_slot"])
    if first.tzinfo is None or due_window(mode, first) is None or due_window(mode, first)[0] != first:
        raise ValueError("options_activation_slot")
    if slot < first:
        return {"state": "not_due", "data_requests": 0}
    if start < date.fromisoformat(policy["history_floor"]):
        raise ValueError("options_history_floor")
    os.umask(0o077)
    with (base / "scheduled.lock").open("a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("options_scheduled_run_active") from None
        namespace = period_namespace(mode, policy)
        work = base / namespace / end.isoformat()
        if (work / "result.json").exists():
            return json.loads((work / "result.json").read_text())
        if (work / "started.json").exists():
            raise RuntimeError("options_period_requires_reconciliation")
        # Fail before durable admission if the store is missing or not fully migrated.
        with StoreWriteLock(store.path, timeout_seconds=10):
            with store.read() as connection:
                store._check(connection, require_latest=True)
        work.mkdir(parents=True, exist_ok=True)
        # No request, authentication, restart or retry may precede this record.
        atomic_json(work / "started.json", {
            "contract": policy["contract"], "policy_sha256": policy_sha256,
            "at": now.isoformat(), "slot": slot.isoformat(), "mode": mode,
            "window_start": str(start), "window_end": str(end),
            "bounds": policy[mode], "retries": 0,
        })
        sync_admission_directories(work, root)
        job = ScheduledOptionsRun(root, mode, end, transport_factory=transport_factory, policy=policy)
        result = job.execute(start, end)
        atomic_json(work / "result.json", result)
        atomic_json(base / (mode + "-latest.json"), result)
        return result
