"""Local, bounded health projection for the existing intraday options collector.

No provider, Site, credentials, initialization or writable store connections.
The host fixes the project root; the browser can only select a calendar date.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import json
import os
import sqlite3
import stat
import time

from .errors import QuantDataError
from .options.monitor.policy import NY, CADENCES, load_universe, market_window, radar_offset
from .options.monitor.store import MonitorStore
from .stores import StoreWriteLock

TIMER = "quant-data-options-monitor.timer"
SERVICE = "quant-data-options-monitor.service"
MAX_OPERATIONS = 10_000_000
READ_SECONDS = 2.0


def _instant(value):
    if not isinstance(value, str):
        return None
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if at.tzinfo is None or at.utcoffset() is None:
            return None
        return at.timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def _iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z") if value is not None else None


def _small_json(path, root, maximum):
    if path.resolve() != path or not path.is_relative_to(root):
        raise ValueError("unsafe_monitor_metadata")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size <= maximum:
            raise ValueError("invalid_monitor_metadata")
        raw = os.read(fd, maximum + 1)
        after = os.fstat(fd)
        if len(raw) != before.st_size or (before.st_mtime_ns, before.st_ctime_ns) != (after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError("changed_monitor_metadata")
        return json.loads(raw)
    finally:
        os.close(fd)


def _runtime(unit_output, now):
    from .inspector_fetch_status import _system_time
    if unit_output is None:
        return {}, {}
    props = {}
    for block in unit_output.strip().split("\n\n"):
        fields = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        if fields.get("Id") in (TIMER, SERVICE):
            props[fields["Id"]] = fields
    timer, service = props.get(TIMER, {}), props.get(SERVICE, {})
    scheduler = {"state": "unknown", "next_run_at": None}
    if timer.get("LoadState") == "loaded":
        scheduler["state"] = "enabled" if timer.get("ActiveState") == "active" else "disabled"
        next_at = _system_time(timer.get("NextElapseUSecRealtime", ""))
        if scheduler["state"] == "enabled" and next_at and next_at > now:
            scheduler["next_run_at"] = _iso(next_at.timestamp())
    completed = _system_time(service.get("ExecMainExitTimestamp", ""))
    started = _system_time(service.get("ExecMainStartTimestamp", ""))
    settled = bool(completed and started and started <= completed <= now and service.get("ActiveState") != "activating")
    success = settled and service.get("Result") == "success" and service.get("ExecMainCode") == "1" and service.get("ExecMainStatus") == "0"
    return scheduler, {
        "last_completed_at": _iso(completed.timestamp()) if success else None,
        "failed_at": completed.timestamp() if settled and not success else None,
        "running": service.get("LoadState") == "loaded" and service.get("ActiveState") == "activating",
    }


def _unavailable(now, selected, reason):
    return {
        "available": False, "checked_at": _iso(now.timestamp()), "operating_date": now.astimezone(NY).date().isoformat(),
        "reason": reason,
        "collection": {"state": "unknown", "last_observed_at": None, "last_completed_at": None,
                       "last_attempt_at": None, "symbols_observed": None, "symbols_configured": None,
                       "fresh_symbols": None, "pending_symbols": None, "failed_requests": None,
                       "unresolved_requests": None, "message": reason},
        "delivery": {"state": "unknown", "pending_count": None, "pending_alerts": None,
                     "last_upload_attempt_at": None, "message": "Upload queue evidence is unavailable."},
        "cadence": [],
        "session": {"state": "unknown", "label": "Session not established"},
        "scheduler": {"state": "unknown", "next_run_at": None},
        "selected_session": {"date": selected, "observations": None, "symbols": None,
                             "failed_requests": None, "unresolved_requests": None, "last_observed_at": None,
                             "note": "Saved evidence is unavailable; missing records are not zero activity."},
    }


def _session_rows(c, session, cutoff):
    # Single MAX binds cadence/capture text to a newest row; conversion stays
    # inside SQLite so a health check does not parse every retained row in Python.
    rows = c.execute(
        "SELECT symbol,cadence,captured_at,MAX(julianday(captured_at)) AS latest,COUNT(*) AS observations "
        "FROM observations WHERE session=? AND julianday(captured_at)<=julianday(?, 'unixepoch') "
        "AND (captured_at LIKE '%Z' OR captured_at GLOB '*[+-][0-2][0-9]:[0-5][0-9]') "
        "GROUP BY symbol LIMIT 3001", (session, cutoff)).fetchall()
    if len(rows) > 3000 or any(row["cadence"] not in CADENCES for row in rows):
        raise ValueError("monitor_observation_scope")
    result = []
    for row in rows:
        latest = _instant(row["captured_at"])
        if latest is None or latest > cutoff:
            raise ValueError("monitor_observation_clock")
        result.append({**dict(row), "latest": latest})
    return result


def _attempts(c, session, cutoff):
    rows = c.execute(
        "SELECT outcome,COUNT(*) AS count,started_at,MAX(julianday(started_at)) AS latest,"
        "SUM(CASE WHEN julianday(started_at)<julianday(?, 'unixepoch') THEN 1 ELSE 0 END) AS overdue "
        "FROM attempts WHERE session=? AND julianday(started_at)<=julianday(?, 'unixepoch') "
        "AND (started_at LIKE '%Z' OR started_at GLOB '*[+-][0-2][0-9]:[0-5][0-9]') "
        "GROUP BY outcome LIMIT 101", (cutoff - 300, session, cutoff)).fetchall()
    if len(rows) > 100:
        raise ValueError("monitor_attempt_scope")
    return {
        "failed": sum(row["count"] for row in rows if row["outcome"] not in {"success", "empty", "no_data", "started"}),
        "unresolved": sum(row["overdue"] for row in rows if row["outcome"] == "started"),
        "last_at": max((_instant(row["started_at"]) for row in rows), default=None),
    }


def _window(day, rows):
    if day.weekday() >= 5:
        return None
    return market_window(day, rows, extended=True)


def _project(now, selected, universe, latest_session, current_rows, selected_rows, attempts, selected_attempts,
             calendar_rows, queue, scheduler, runtime, pause):
    today = now.astimezone(NY).date()
    result = _unavailable(now, selected, "")
    result.update(available=True, reason="")
    result["scheduler"] = scheduler or result["scheduler"]
    known = [row for row in current_rows if row["symbol"] in universe]
    current = latest_session == today.isoformat()
    window = None
    try:
        window = _window(today, calendar_rows)
        session_state = "open" if window and window[0] <= now <= window[1] else "closed"
    except (ValueError, TypeError):
        local = now.astimezone(NY)
        session_state = "closed" if local.weekday() >= 5 or local.hour < 9 or (local.hour == 9 and local.minute < 30) or (local.hour * 60 + local.minute > 975) else "unknown"
    result["session"] = {"state": session_state,
                         "label": {"open": "Trading session", "closed": "Market closed", "unknown": "Market calendar unavailable"}[session_state]}
    latest = max((row["latest"] for row in known), default=None)
    fresh, overdue, grace = 0, 0, 0
    by_symbol = {row["symbol"]: row for row in known} if current else {}
    if session_state == "open":
        for symbol, member in universe.items():
            opening, closing = window
            if symbol not in {"SPY", "QQQ", "IWM", "DIA"}:
                closing -= timedelta(minutes=15)
            if now > closing:
                continue
            row = by_symbol.get(symbol)
            if row:
                if now.timestamp() - row["latest"] <= (row["cadence"] + 5) * 60:
                    fresh += 1
                else:
                    overdue += 1
            else:
                offset = radar_offset(symbol) if member["group"] == "equity" and member["cadence_minutes"] == 30 else 0
                due = opening + timedelta(minutes=offset + member["cadence_minutes"] + 5)
                if now > due:
                    overdue += 1
                else:
                    grace += 1
    state, message = "unknown", "Current collection health could not be established."
    if pause:
        state, message = "paused", "Collection is intentionally paused until " + pause["until"] + "."
    elif session_state == "closed":
        state, message = "closed", "No intraday pulls are due outside the trading session. Saved coverage remains visible below."
    elif session_state == "open":
        if overdue:
            state, message = "stale", "Some configured symbols are overdue for an observation."
        elif attempts["unresolved"]:
            state, message = "stale", "Some earlier requests have no recorded completion."
        elif fresh:
            state, message = "current", "Observed symbols are within their saved cadence, with a five-minute collection allowance."
        else:
            state, message = "waiting", "Waiting for the first scheduled observations of this session."
        if runtime.get("failed_at") and runtime["failed_at"] >= (latest or 0):
            state, message = "failed", "The latest collector cycle failed. Saved observations and queued uploads are retained."
        elif result["scheduler"]["state"] == "disabled":
            state, message = "failed", "The collection timer is inactive during the trading session."
    result["collection"] = {
        "state": state, "last_observed_at": _iso(latest), "last_completed_at": runtime.get("last_completed_at"),
        "last_attempt_at": _iso(attempts["last_at"]), "symbols_observed": len(known) if current else 0,
        "symbols_configured": len(universe), "fresh_symbols": fresh if session_state == "open" else None,
        "pending_symbols": overdue if session_state == "open" else None,
        "failed_requests": attempts["failed"], "unresolved_requests": attempts["unresolved"], "message": message,
        "observation_session": latest_session,
    }
    pending, alerts, upload_attempt = queue
    result["delivery"] = {
        "state": "pending" if pending or alerts else ("current" if latest is not None else "unknown"),
        "pending_count": pending, "pending_alerts": alerts, "last_upload_attempt_at": _iso(upload_attempt),
        "message": ("Saved items are awaiting dashboard acknowledgment." if pending or alerts else
                    "No saved items await upload. This is local queue evidence, not a remote dashboard check."),
    }
    for minutes in (5, 10, 15, 30):
        band = [row for row in known if current and row["cadence"] == minutes]
        band_last = max((row["latest"] for row in band), default=None)
        band_state = "closed" if session_state == "closed" else "unknown"
        if session_state == "open" and current and band:
            band_state = "current" if all(now.timestamp() - row["latest"] <= (minutes + 5) * 60 for row in band) else "stale"
        result["cadence"].append({"minutes": minutes, "state": band_state,
                                  "symbols_observed": len(band), "last_observed_at": _iso(band_last)})
    if selected_rows is None:
        result["selected_session"]["note"] = "Historical detail exceeded the bounded read allowance; its counts remain unconfirmed."
        return result
    result["selected_session"] = {
        "date": selected, "observations": sum(row["observations"] for row in selected_rows),
        "symbols": len(selected_rows), "failed_requests": selected_attempts["failed"],
        "unresolved_requests": selected_attempts["unresolved"],
        "last_observed_at": _iso(max((row["latest"] for row in selected_rows), default=None)),
        "note": "Saved observations for the selected date. Request details are retained for seven sessions; older absent details are unconfirmed.",
    }
    return result


def read_intraday_options(root: Path | None, selected_date: str, *, observed_at: datetime,
                          unit_output: str | None = None, probe: bool = False):
    from .inspector_fetch_status import _command, _clock, selected_status_date
    now = _clock(observed_at)
    selected = selected_status_date(selected_date, now).isoformat()
    result = _unavailable(now, selected, "Intraday monitor evidence is unavailable.")
    if root is None:
        return result
    if probe:
        unit_output = _command(["/usr/bin/systemctl", "--user", "show", "--no-pager", "--all",
            "--property=Id,LoadState,ActiveState,SubState,ExecMainStartTimestamp,ExecMainExitTimestamp,ExecMainCode,ExecMainStatus,Result,NextElapseUSecRealtime",
            TIMER, SERVICE], 16384)
    scheduler, runtime = _runtime(unit_output, now)
    result["scheduler"] = scheduler or result["scheduler"]
    try:
        root = Path(root)
        if not root.is_absolute() or root.resolve(strict=True) != root:
            raise ValueError("monitor_root")
        roster = root / "config/options_monitor_universe.json"
        _small_json(roster, root, 1048576)
        universe = load_universe(roster)
        store = MonitorStore(root)
        if not store.path.is_file():
            raise ValueError("monitor_store_missing")
        today = now.astimezone(NY).date().isoformat()
        pause = None
        pause_path = store.path.parent / ("live-pause-" + today + ".json")
        if pause_path.exists():
            from .options.monitor.admission import ThetaAdmission
            pause = _small_json(pause_path, root, 1024)
            if pause != ThetaAdmission(root)._pause_value(now, pause.get("backfill_run_id")):
                raise ValueError("monitor_pause_invalid")
        with StoreWriteLock(store.path, timeout_seconds=.25), store.read() as c:
            start = time.monotonic()
            operations = [0]
            def progress():
                operations[0] += 1000
                return int(operations[0] > MAX_OPERATIONS or time.monotonic() - start > READ_SECONDS)
            c.set_progress_handler(progress, 1000)
            c.create_function("monitor_instant", 1, _instant, deterministic=True)
            cutoff = now.timestamp()
            row = c.execute("SELECT session FROM observations WHERE session<=? AND monitor_instant(captured_at)<=? ORDER BY session DESC LIMIT 1", (today, cutoff)).fetchone()
            latest_session = row[0] if row else today
            current_rows = _session_rows(c, latest_session, cutoff)
            attempts = _attempts(c, today, cutoff)
            calendar = c.execute("SELECT CASE WHEN length(payload)<=65536 THEN payload END FROM calendar WHERE session=?", (today,)).fetchone()
            calendar_rows = json.loads(calendar[0]) if calendar and calendar[0] else []
            queue = c.execute(
                "SELECT COALESCE(SUM(kind='observation'),0),COALESCE(SUM(kind='alert'),0),"
                "MAX(monitor_instant(attempted_at)) FROM outbox WHERE published=0").fetchone()
            # Optional historical detail shares the remaining two-second budget.
            # An interrupted historical scan must not erase established current health.
            selected_rows = None
            selected_attempts = {"failed": None, "unresolved": None}
            try:
                selected_rows = current_rows if selected == latest_session else _session_rows(c, selected, cutoff)
                selected_attempts = attempts if selected == today else _attempts(c, selected, cutoff)
                oldest = c.execute("SELECT session FROM attempts ORDER BY session LIMIT 1").fetchone()
                if selected > today or (selected < today and (not oldest or selected < oldest[0])):
                    selected_attempts = {**selected_attempts, "failed": None, "unresolved": None}
            except sqlite3.OperationalError as exc:
                if getattr(exc, "sqlite_errorcode", None) != sqlite3.SQLITE_INTERRUPT:
                    raise
                selected_rows = None
                selected_attempts = {"failed": None, "unresolved": None}
        return _project(now, selected, universe, latest_session, current_rows, selected_rows, attempts, selected_attempts,
                        calendar_rows, tuple(queue), scheduler, runtime, pause)
    except (OSError, ValueError, TypeError, OverflowError, sqlite3.Error, QuantDataError):
        return result


def attach_intraday_options(snapshot, root):
    """Host binding only; never accepts a root, query or provider from HTTP."""
    value = dict(snapshot)
    observed = datetime.fromisoformat(value["observed_at"].replace("Z", "+00:00"))
    value["intraday_options"] = read_intraday_options(root, value["selected_date"], observed_at=observed, probe=True)
    value["intraday_options"]["calendar_days"] = read_intraday_calendar(
        root, [day["date"] for day in value.get("days", [])], observed_at=observed, status=value["intraday_options"])
    return value


CALENDAR_READ_SECONDS = 3.0


def _calendar_projection(days, now, status, outcomes, calendars, available):
    today = now.astimezone(NY).date().isoformat()
    result = []
    for day in days:
        row = outcomes.get(day)
        item = {
            "date": day, "state": "unknown", "label": "Unconfirmed",
            "message": "Request history is unavailable or no longer retained for this date.",
            "successful_requests": row["successful"] if row else None,
            "failed_requests": row["failed"] if row else None,
            "unresolved_requests": row["unresolved"] if row else None,
            "last_attempt_at": row["last_attempt_at"] if row else None,
        }
        if row:
            item.update(state="recorded", label="Requests recorded",
                        message="Successful provider requests were recorded, including any no-data responses. This does not prove complete session coverage.")
            if row["unresolved"]:
                item.update(state="stale", label="Unfinished requests",
                            message="Earlier requests have no recorded completion.")
            if row["failed"]:
                item.update(state="partial" if row["successful"] else "failed",
                            label="Some requests failed" if row["successful"] else "Requests failed",
                            message="Recorded failures are retained even when other requests succeeded.")
        elif available:
            closed = date.fromisoformat(day).weekday() >= 5
            if day in calendars:
                try:
                    closed = _window(date.fromisoformat(day), calendars[day]) is None
                except (ValueError, TypeError):
                    pass
            if closed:
                item.update(state="closed", label="Market closed",
                            message="No trading-session pulls were scheduled for this day.")
            elif day > today:
                item.update(state="scheduled", label="Planned",
                            message="Weekday intraday collection is planned, subject to the market calendar.")
        if day == today:
            collection = status.get("collection", {})
            if status.get("available"):
                item.update(state=collection.get("state", "unknown"),
                            message=collection.get("message", "Current health is unconfirmed."))
                labels = {"current": "Current", "stale": "Overdue", "failed": "Collection failed",
                          "waiting": "Waiting", "closed": "Market closed", "paused": "Paused",
                          "unknown": "Unconfirmed"}
                item["label"] = labels.get(item["state"], "Unconfirmed")
                # Day outcomes survive a later market close; closed is not a
                # substitute for an earlier recorded failure or incomplete pull.
                if item["state"] in {"closed", "current"} and row and (row["failed"] or row["unresolved"]):
                    item.update(state="partial" if row["successful"] else "failed", label="Needs attention",
                                message="The selected session contains failed or unfinished requests.")
            else:
                item.update(state="unknown", label="Health unconfirmed",
                            message="Current collector health could not be established.")
        result.append(item)
    return result


def read_intraday_calendar(root, days, *, observed_at, status):
    """Bounded request-outcome summaries for one displayed calendar month.

    The retained attempts table contains compact metadata, not option payloads
    or the baseline receipt ledger. No request success is treated as proof of
    complete observation coverage or remote publication.
    """
    now = observed_at.astimezone(timezone.utc)
    days = sorted(set(days))
    if not days:
        return []
    if len(days) > 31 or any(date.fromisoformat(day).strftime("%Y-%m") != days[0][:7] for day in days):
        raise ValueError("intraday_calendar_scope")
    outcomes, calendars, available = {}, {}, False
    try:
        if root is None:
            raise ValueError("monitor_root")
        root = Path(root)
        if not root.is_absolute() or root.resolve(strict=True) != root:
            raise ValueError("monitor_root")
        store = MonitorStore(root)
        if not store.path.is_file():
            raise ValueError("monitor_store_missing")
        with StoreWriteLock(store.path, timeout_seconds=.25), store.read() as c:
            start = time.monotonic()
            operations = [0]
            def progress():
                operations[0] += 1000
                return int(operations[0] > 20_000_000 or time.monotonic() - start > CALENDAR_READ_SECONDS)
            c.set_progress_handler(progress, 1000)
            # Avoid a whole retained-table scan for an empty historical month.
            present = c.execute("SELECT 1 FROM attempts WHERE session BETWEEN ? AND ? LIMIT 1",
                                (days[0], days[-1])).fetchone()
            rows = []
            if present:
                rows = c.execute(
                    "SELECT session,SUM(outcome IN ('success','empty','no_data')) AS successful,"
                    "SUM(outcome NOT IN ('success','empty','no_data','started')) AS failed,"
                    "SUM(outcome='started' AND julianday(started_at)<julianday(?,'unixepoch')) AS unresolved,"
                    "started_at,MAX(julianday(started_at)) AS latest "
                    "FROM attempts NOT INDEXED WHERE session BETWEEN ? AND ? "
                    "AND julianday(started_at)<=julianday(?,'unixepoch') "
                    "AND (started_at LIKE '%Z' OR started_at GLOB '*[+-][0-2][0-9]:[0-5][0-9]') "
                    "GROUP BY session LIMIT 32",
                    (now.timestamp() - 300, days[0], days[-1], now.timestamp())).fetchall()
            outcomes = {row["session"]: {
                "successful": row["successful"], "failed": row["failed"], "unresolved": row["unresolved"],
                "last_attempt_at": _iso(_instant(row["started_at"])),
            } for row in rows}
            for row in c.execute("SELECT session,CASE WHEN length(payload)<=65536 THEN payload END AS payload "
                                 "FROM calendar WHERE session BETWEEN ? AND ? LIMIT 32", (days[0], days[-1])):
                if row["payload"]:
                    calendars[row["session"]] = json.loads(row["payload"])
        available = True
    except (OSError, ValueError, TypeError, OverflowError, sqlite3.Error, QuantDataError):
        outcomes, calendars = {}, {}
    return _calendar_projection(days, now, status, outcomes, calendars, available)
