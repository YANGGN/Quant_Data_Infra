"""Read-only execution calendar for the ten fixed local fetch batches.

Current calendar rules describe planned slots, not historical activation proof.
Private collector run receipts and completed systemd oneshot results establish
outcomes. No provider logs, credentials, canonical stores or writes participate.
"""
from __future__ import annotations

from calendar import monthrange
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import os
from pathlib import Path
import re
import subprocess
from typing import Any
from zoneinfo import ZoneInfo

from .errors import QuantDataError, ValidationError
from .inspector_schedules import TIMER_BINDINGS, _CALENDAR, _WEEKDAY, _property
from .json_codec import loads_strict

EASTERN = ZoneInfo("America/New_York")
_SERVICES = {binding.unit.removesuffix(".timer") + ".service" for binding in TIMER_BINDINGS}
_UNITS = _SERVICES | {binding.unit for binding in TIMER_BINDINGS}
_PROPERTIES = "Id,LoadState,ActiveState,SubState,Type,TimersCalendar,ExecMainStartTimestamp,ExecMainExitTimestamp,ExecMainCode,ExecMainStatus,Result,InvocationID,NextElapseUSecRealtime"
_JOURNAL_FIELDS = "__REALTIME_TIMESTAMP,_BOOT_ID,_UID,_PID,_COMM,_EXE,USER_INVOCATION_ID,USER_UNIT,JOB_ID,JOB_TYPE,JOB_RESULT"
_JOURNAL_LIMIT = 2000
_DESCRIPTIONS = {
    "Market close": "Daily equity, ETF and index prices",
    "ETF options": "Option surfaces for the fixed ETF universe",
    "Macro current": "Treasury, Fed, NY Fed, energy and economic indicators",
    "GDP / CPI": "Official GDP and inflation vintages",
    "Employment": "Payroll and unemployment vintages",
    "Economic calendar": "Release calendar and normalized economic events",
    "SEC fundamentals": "Company filings and financial facts",
    "Company market": "Dividends, splits and analyst estimates",
    "Equibles transcripts": "Raw earnings-call transcripts for the retained stock universe",
    "Current news": "Headlines from the configured current news sources",
}


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _clock(value: datetime | None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if not isinstance(result, datetime) or result.tzinfo is None or result.utcoffset() is None:
        raise ValidationError("Status observation time must include an offset")
    return result.astimezone(timezone.utc)


def selected_status_date(value: str | None, now: datetime) -> date:
    if value is None:
        return now.astimezone(EASTERN).date()
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        raise ValidationError("Status date must be YYYY-MM-DD")
    try:
        selected = date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError("Status date is invalid") from exc
    if not date(1900, 1, 1) <= selected <= date(9998, 12, 31):
        raise ValidationError("Status date is outside the supported range")
    return selected


def _properties(output: str) -> dict[str, dict[str, list[str]]]:
    result = {}
    for block in output.strip().split("\n\n"):
        fields: dict[str, list[str]] = {}
        for line in block.splitlines():
            name, separator, value = line.partition("=")
            if separator:
                fields.setdefault(name, []).append(value)
        identity = _property(fields, "Id")
        if identity in _UNITS:
            result[identity] = fields
    return result


def _command(command: list[str], maximum: int) -> str | None:
    try:
        response = subprocess.run(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
            timeout=3, check=False, shell=False,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "TZ": "UTC",
                 "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if response.returncode != 0 or len(response.stdout.encode("utf-8")) > maximum:
        return None
    return response.stdout


def _system_time(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%a %Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _slots(calendars: list[str], start: date, days: int = 7) -> tuple[list[datetime], bool]:
    """Expand only exact supported loaded calendar forms, preserving DST."""
    result: set[datetime] = set()
    supported = bool(calendars)
    first = datetime.combine(start, time(), EASTERN).astimezone(timezone.utc)
    last = datetime.combine(start + timedelta(days=days), time(), EASTERN).astimezone(timezone.utc)
    for entry in calendars:
        match = _CALENDAR.fullmatch(entry)
        calendar = match[1] if match else ""
        weekday = _WEEKDAY.fullmatch(calendar)
        if weekday:
            hour, minute = map(int, weekday[1].split(":"))
            if hour > 23 or minute > 59:
                supported = False
                continue
            for offset in range(days):
                day = start + timedelta(days=offset)
                if day.weekday() < 5:
                    result.add(datetime.combine(day, time(hour, minute), EASTERN).astimezone(timezone.utc))
        elif calendar == "Fri *-*-01..07 10:05:00 America/New_York":
            for offset in range(days):
                day = start + timedelta(days=offset)
                if day.weekday() == 4 and day.day <= 7:
                    result.add(datetime.combine(day, time(10, 5), EASTERN).astimezone(timezone.utc))
        elif calendar == "*-*-* 00:10:00 UTC":
            instant = first.replace(hour=0, minute=10, second=0, microsecond=0)
            while instant < last:
                if instant >= first:
                    result.add(instant)
                instant += timedelta(days=1)
        elif calendar == "*-*-* *:10:00 UTC":
            instant = first.replace(minute=10, second=0, microsecond=0)
            while instant < last:
                if instant >= first:
                    result.add(instant)
                instant += timedelta(hours=1)
        else:
            supported = False
    return sorted(result), supported


@dataclass(frozen=True)
class _Run:
    service: str
    started: datetime
    finished: datetime | None
    status: str
    invocation: str
    evidence: str = "systemd"
    exit_code: int | None = None
    run_id: str = ""
    counts: dict[str, Any] | None = None


def _journal_runs(output: str, now: datetime) -> list[_Run]:
    groups: dict[tuple[str, ...], list[tuple[datetime, str, str]]] = defaultdict(list)
    for line in output.splitlines():
        try:
            row = loads_strict(line, max_bytes=16_384)
        except QuantDataError:
            continue
        if not isinstance(row, dict) or not all(isinstance(row.get(key), str) for key in ("_COMM", "_UID", "_EXE", "USER_UNIT", "JOB_TYPE")):
            continue
        if (row.get("_COMM") != "systemd" or row.get("_UID") != str(os.getuid())
                or row.get("_EXE") not in {"/usr/lib/systemd/systemd", "/lib/systemd/systemd"}
                or row.get("USER_UNIT") not in _SERVICES or row.get("JOB_TYPE") != "start"):
            continue
        identity = tuple(row.get(key) for key in ("USER_UNIT", "_BOOT_ID", "_PID", "JOB_ID"))
        stamp = row.get("__REALTIME_TIMESTAMP")
        outcome = row.get("JOB_RESULT", "")
        invocation = row.get("USER_INVOCATION_ID", "")
        if (not all(isinstance(item, str) and item for item in identity)
                or not isinstance(stamp, str) or not stamp.isdecimal() or len(stamp) > 18
                or not isinstance(outcome, str) or not isinstance(invocation, str)):
            continue
        try:
            instant = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=int(stamp))
        except OverflowError:
            continue
        if instant <= now:
            groups[identity].append((instant, outcome, invocation))
    runs = []
    for key, entries in groups.items():
        starts = [entry for entry in entries if entry[1] == ""]
        ends = [entry for entry in entries if entry[1] != ""]
        if len(starts) != 1 or len(ends) != 1 or ends[0][0] < starts[0][0]:
            continue
        started, _, invocation = starts[0]
        finished, outcome, end_invocation = ends[0]
        if invocation and end_invocation and invocation != end_invocation:
            continue
        if outcome == "done":
            status = "succeeded"
        elif outcome in {"failed", "timeout", "dependency", "assert", "unsupported"}:
            status = "failed"
        else:
            continue
        runs.append(_Run(key[0], started, finished, status, invocation or end_invocation))
    return runs


def _latest_run(service: str, properties: dict[str, list[str]], now: datetime) -> _Run | None:
    if _property(properties, "Type") != "oneshot" or _property(properties, "LoadState") != "loaded":
        return None
    started = _system_time(_property(properties, "ExecMainStartTimestamp"))
    finished = _system_time(_property(properties, "ExecMainExitTimestamp"))
    if started is None or started > now:
        return None
    invocation = _property(properties, "InvocationID")
    if _property(properties, "ActiveState") == "activating":
        return _Run(service, started, None, "running", invocation)
    if finished is None or not started <= finished <= now:
        return None
    result = _property(properties, "Result")
    settled = (_property(properties, "ActiveState"), _property(properties, "SubState")) in {("inactive", "dead"), ("active", "exited")}
    if settled and result == "success" and _property(properties, "ExecMainCode") == "1" and _property(properties, "ExecMainStatus") == "0":
        return _Run(service, started, finished, "succeeded", invocation)
    if result in {"exit-code", "signal", "core-dump", "timeout", "watchdog", "resources", "oom-kill", "start-limit-hit", "protocol"}:
        return _Run(service, started, finished, "failed", invocation)
    return None


def _merge_saved_run(runs: list[_Run], saved: _Run) -> None:
    """Join only an exact invocation; separate attempts must remain ambiguous."""
    duplicates = [index for index, run in enumerate(runs)
                  if run.evidence == "systemd" and saved.invocation
                  and run.service == saved.service and run.invocation == saved.invocation]
    if len(duplicates) == 1:
        other = runs.pop(duplicates[0])
        terminal = other.status in {"succeeded", "failed"} and other.finished is not None and other.finished >= saved.started
        if other.status == "unconfirmed":
            saved = _Run(saved.service, saved.started, None, "unconfirmed",
                         saved.invocation, "conflicting_results", None, saved.run_id)
        elif saved.status == "unconfirmed" and terminal:
            saved = _Run(saved.service, saved.started, other.finished, other.status,
                         saved.invocation, "saved_start_systemd", other.exit_code, saved.run_id)
        elif terminal and saved.status in {"succeeded", "failed"} and other.status != saved.status:
            saved = _Run(saved.service, saved.started, None, "unconfirmed",
                         saved.invocation, "conflicting_results", None, saved.run_id)
    runs.append(saved)


def _event(binding: Any, slot: datetime, runs: list[_Run], now: datetime) -> dict[str, Any]:
    service = binding.unit.removesuffix(".timer") + ".service"
    matches = [run for run in runs if run.service == service and slot <= run.started < slot + timedelta(minutes=5)]
    status = "upcoming" if slot > now else "unconfirmed"
    started = finished = None
    evidence, exit_code = "unconfirmed", None
    work_counts = None
    note = "Planned batch start; no execution result yet." if status == "upcoming" else "No matching completed run is recorded. Missing history is not proof of failure."
    if len(matches) == 1 and slot <= now:
        run = matches[0]
        status, started, finished = run.status, _utc(run.started), _utc(run.finished) if run.finished else None
        note = {
            "succeeded": "Recorded batch completed successfully. Source data may have been unchanged or a holiday check may have skipped fetching.",
            "failed": "Recorded batch failed. Some sources or data may still have been stored.",
            "running": "The batch is currently running; its final outcome is not yet known.",
            "unconfirmed": "Recorded execution sources disagree; the final outcome is unconfirmed.",
        }[status] + " Matched by a start within five minutes of this slot."
        evidence, exit_code = run.evidence, run.exit_code
        if run.evidence == "saved_run":
            note = {
                "succeeded": "Saved run completed successfully (exit code 0). Source data may be unchanged or a holiday check may have skipped fetching.",
                "failed": f"Saved run failed (exit code {run.exit_code}). Some sources or data may still have been stored.",
                "running": "Saved run is in progress; the original process is still running.",
                "unconfirmed": "A saved start has no completed result, and its original process cannot be confirmed as running.",
            }[status] + " Matched by a start within five minutes of this slot."
        elif run.evidence == "saved_start_systemd":
            note = ("The collector saved its start but no completion. Systemd recorded "
                    + ("a successful completion" if status == "succeeded" else "a failure")
                    + " for the same invocation. Matched by a start within five minutes of this slot.")
        if run.status in {"succeeded", "failed"} and run.finished is not None:
            work_counts = run.counts
            if run.status == "failed" and work_counts and work_counts["successful"] + work_counts["partial"] > 0:
                status = "partial"
                note = ("Recorded run completed some work but did not finish cleanly. "
                        "The counts below distinguish successful, failed and incomplete work. "
                        "Matched by a start within five minutes of this slot.")
    elif len(matches) > 1 and slot <= now:
        note = "Multiple runs match this scheduled slot; its outcome is unconfirmed."
    local = slot.astimezone(EASTERN)
    return {
        "id": binding.unit + ":" + _utc(slot), "batch_id": binding.unit, "label": binding.label,
        "description": _DESCRIPTIONS[binding.label], "scheduled_at": _utc(slot),
        "scheduled_local": local.strftime("%H:%M %Z"), "date": local.date().isoformat(),
        "status": status, "status_label": {"succeeded": "Completed", "partial": "Partial Success", "failed": "Failed", "running": "Running", "upcoming": "Scheduled", "unconfirmed": "Unconfirmed"}[status],
        "color": "green" if status == "succeeded" else "red" if status == "failed" else "amber",
        "started_at": started, "finished_at": finished, "note": note,
        "evidence": evidence, "exit_code": exit_code, "counts": work_counts,
        "datasets": list(binding.datasets),
    }


def _month_selection(selected: date, offset: int) -> str | None:
    month_index = selected.year * 12 + selected.month - 1 + offset
    year, month_zero = divmod(month_index, 12)
    if not 1900 <= year <= 9998:
        return None
    month = month_zero + 1
    return date(year, month, min(selected.day, monthrange(year, month)[1])).isoformat()


def _day_markers(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One batch/day marker: any recorded failure remains visible after a retry."""
    markers = []
    for binding in TIMER_BINDINGS:
        batch_events = [event for event in events if event["batch_id"] == binding.unit]
        if not batch_events:
            continue
        counts = Counter(event["status"] for event in batch_events)
        status = next(state for state in ("failed", "partial", "running", "unconfirmed", "upcoming", "succeeded") if counts[state])
        markers.append({
            "batch_id": binding.unit, "label": binding.label, "status": status,
            "status_label": {"succeeded": "Completed", "partial": "Partial Success", "failed": "Failed", "running": "Running", "upcoming": "Scheduled", "unconfirmed": "Unconfirmed"}[status],
            "color": "green" if status == "succeeded" else "red" if status == "failed" else "amber",
            "run_count": len(batch_events),
            "counts": {state: counts[state] for state in ("succeeded", "partial", "failed", "running", "upcoming", "unconfirmed")},
        })
    return markers


def read_fetch_status(selected_date: str | None = None, *, observed_at: datetime | None = None,
                      unit_output: str | None = None, journal_output: str | None = None,
                      probe: bool = True, history_root: Path | None = None,
                      equibles_root: Path | None = None) -> dict[str, Any]:
    """Read at most one unit snapshot and one calendar-month metadata-only journal query.

    Fixtures supply both outputs with probe=False, so tests never use host defaults.
    Only a validated date enters the fixed query window; callers cannot select units.
    Saved run history is read only when an explicit host-selected root is supplied.
    """
    now = _clock(observed_at)
    selected = selected_status_date(selected_date, now)
    month_start = selected.replace(day=1)
    day_count = monthrange(selected.year, selected.month)[1]
    month_end = selected.replace(day=day_count)
    first = datetime.combine(month_start, time(), EASTERN).astimezone(timezone.utc)
    last = datetime.combine(month_end + timedelta(days=1), time(), EASTERN).astimezone(timezone.utc)
    if probe:
        unit_output = _command(["/usr/bin/systemctl", "--user", "show", "--no-pager", "--all",
                                "--property=" + _PROPERTIES, *sorted(_UNITS)], 131_072)
        journal_output = ""
        if first <= now:
            journal_output = _command([
                "/usr/bin/journalctl", "--user", "--no-pager", "--quiet", "--output=json",
                "--output-fields=" + _JOURNAL_FIELDS, "--lines=" + str(_JOURNAL_LIMIT),
                "--since=" + first.strftime("%Y-%m-%d %H:%M:%S UTC"),
                "--until=" + min(last, now).strftime("%Y-%m-%d %H:%M:%S UTC"),
                *["--user-unit=" + service for service in sorted(_SERVICES)],
                "_COMM=systemd", "JOB_TYPE=start",
            ], 2_097_152)
    notices = ["Times are Eastern (EST/EDT). Calendar slots use currently loaded schedules; historical activation and manual versus timer origin are not inferred."]
    if unit_output is None:
        notices.append("Local schedule and execution metadata are unavailable.")
    if journal_output is None:
        notices.append("Execution history is unavailable. Saved collector receipts and independently recorded latest service results can still confirm a past slot.")
    elif len(journal_output.splitlines()) >= _JOURNAL_LIMIT:
        notices.append("Execution history reached its record limit; older slots may remain unconfirmed.")
    properties = _properties(unit_output or "")
    runs = _journal_runs(journal_output or "", now)
    for service in sorted(_SERVICES):
        latest = _latest_run(service, properties.get(service, {}), now)
        if latest is None:
            continue
        duplicates = [index for index, run in enumerate(runs) if run.service == service and (
            (latest.invocation and run.invocation == latest.invocation)
            or (not run.invocation and abs((run.started - latest.started).total_seconds()) < 1))]
        if not duplicates:
            runs.append(latest)
        elif len(duplicates) == 1 and runs[duplicates[0]].status != latest.status:
            original = runs[duplicates[0]]
            runs[duplicates[0]] = _Run(service, original.started, original.finished, "unconfirmed", original.invocation)
    if history_root is not None:
        from .operations.fetch_run_history import read_fetch_run_history
        history = read_fetch_run_history(history_root, start=first, end=last, observed_at=now)
        notices.append("Run outcomes use saved collector receipts with available systemd history. Earlier unrecorded runs remain unconfirmed.")
        if not history["available"]:
            notices.append("Saved run history is unavailable or has not been created yet. Future collector runs will create their own records.")
        if history["truncated"] or history["invalid"]:
            notices.append("Some saved run history is incomplete or could not be read; affected slots may remain unconfirmed.")
        for record in history["records"]:
            _merge_saved_run(runs, _Run(
                record["batch_id"].removesuffix(".timer") + ".service",
                datetime.fromisoformat(record["started_at"].replace("Z", "+00:00")),
                datetime.fromisoformat(record["finished_at"].replace("Z", "+00:00")) if record["finished_at"] else None,
                record["outcome"], record["invocation_id"], "saved_run", record["exit_code"], record["run_id"], record.get("counts"),
            ))
    events = []
    unavailable = []
    for binding in TIMER_BINDINGS:
        timer = properties.get(binding.unit, {})
        if _property(timer, "LoadState") != "loaded" or _property(timer, "ActiveState") != "active":
            inactive = _property(timer, "LoadState") == "loaded" and _property(timer, "ActiveState") in {"inactive", "failed"}
            unavailable.append({"label": binding.label, "reason": "Timer is inactive; no scheduled slots are shown." if inactive else "Timer schedule is unavailable."})
            continue
        slots, supported = _slots(timer.get("TimersCalendar", []), month_start, day_count)
        if not supported:
            unavailable.append({"label": binding.label, "reason": "Some loaded calendar rules cannot be displayed; no times are invented for them."})
        service = binding.unit.removesuffix(".timer") + ".service"
        service_runs = [run for run in runs if run.evidence != "systemd" or _property(properties.get(service, {}), "Type") == "oneshot"]
        for slot in slots:
            events.append(_event(binding, slot, service_runs, now))
    events.sort(key=lambda event: (event["scheduled_at"], event["label"]))
    today = now.astimezone(EASTERN).date()
    days = []
    for offset in range(day_count):
        day = month_start + timedelta(days=offset)
        day_events = [event for event in events if event["date"] == day.isoformat()]
        counts = Counter(event["color"] for event in day_events)
        days.append({"date": day.isoformat(), "weekday": day.strftime("%a"), "label": day.strftime("%b %d"),
                     "is_today": day == today, "is_selected": day == selected,
                     "counts": {color: counts[color] for color in ("green", "amber", "red")},
                     "markers": _day_markers(day_events), "events": day_events})
    focus = [event for event in events if event["date"] == selected.isoformat()]
    counts = Counter(event["color"] for event in focus)
    from .inspector_equibles import read_equibles_progress
    equibles = read_equibles_progress(equibles_root, observed_at=now)
    eq_timer = properties.get("quant-data-equibles-transcripts.timer", {})
    next_at = _system_time(_property(eq_timer, "NextElapseUSecRealtime"))
    equibles["next_scheduled_at"] = (_utc(next_at) if next_at and next_at > now
        and _property(eq_timer, "ActiveState") == "active" else None)
    return {
        "equibles": equibles,
        "today": today.isoformat(), "selected_date": selected.isoformat(), "observed_at": _utc(now),
        "timezone": "America/New_York", "month_start": month_start.isoformat(), "month_end": month_end.isoformat(),
        "previous_month_date": _month_selection(selected, -1),
        "next_month_date": _month_selection(selected, 1), "days": days,
        "focus_events": focus, "summary": {"total": len(focus),
            "partial": sum(event["status"] == "partial" for event in focus),
            "pending": sum(event["status"] in {"running", "unconfirmed", "upcoming"} for event in focus),
            **{color: counts[color] for color in ("green", "amber", "red")}},
        "notices": notices, "unavailable_jobs": unavailable,
    }
