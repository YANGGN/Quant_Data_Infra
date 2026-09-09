"""Read-only schedule overlay for the local Inspector, separate from tool results.

Only the ten reviewed per-user timers are queried. No service command, provider,
credential, store, or caller-selected unit participates in this projection.
Dataset bindings describe wrapper outputs, not generic registry job declarations.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone

from .data_status import registry_data_status_targets
from .registry import Registry


@dataclass(frozen=True)
class TimerBinding:
    unit: str
    label: str
    datasets: tuple[str, ...]


# These are the output datasets of the fixed wrappers in
# docs/rebuild/SCHEDULING_AND_LOCKING.md. Historical predecessors stay unmapped.
TIMER_BINDINGS = (
    TimerBinding("quant-data-market-close.timer", "Market close", (
        "market.stage10.daily_prices", "market.stage10.source_evidence",
    )),
    TimerBinding("quant-data-alpaca-spy-options.timer", "ETF options", (
        "fixture.market.instruments", "fixture.market.option_capture_evidence",
        "fixture.market.options", "market.alpaca.option_raw_evidence",
    )),
    TimerBinding("quant-data-macro-current-refresh.timer", "Macro current", (
        "fixture.macro.rtdsm_employ_evidence", "fixture.macro.rtdsm_employ",
        "fixture.macro.stage3_catalog", "fixture.macro.treasury_yield_curves",
        "fixture.macro.soma_evidence", "fixture.macro.soma_summary",
        "macro.eia.electricity_retail_history",
        "macro.eia.electricity_retail_history_evidence",
        "macro.eia.petroleum_weekly_stock_history",
        "macro.eia.petroleum_weekly_stock_history_evidence",
    )),
    TimerBinding("quant-data-macro-vintages.timer", "GDP / CPI", (
        "macro.official_vintages", "macro.official_vintages_evidence",
    )),
    TimerBinding("quant-data-employment-vintages.timer", "Employment", (
        "macro.official_vintages", "macro.official_vintages_evidence",
    )),
    TimerBinding("quant-data-fmp-macro-calendar.timer", "Economic calendar", (
        "fixture.macro.economic_calendar",
        "macro.fmp.economic_calendar_incremental_evidence",
        "macro.fmp.economic_calendar_incremental_events",
    )),
    TimerBinding("quant-data-sec-company-fundamentals.timer", "SEC fundamentals", (
        "fixture.company.sec_evidence", "fixture.company.issuers",
        "fixture.company.filings", "fixture.company.fundamentals",
        "fixture.company.filing_issuer_membership",
    )),
    TimerBinding("quant-data-company-market-refresh.timer", "Company market", (
        "fixture.company.action_evidence", "fixture.company.corporate_actions",
        "fixture.company.expectation_evidence", "fixture.company.expectations",
    )),
    TimerBinding("quant-data-equibles-transcripts.timer", "Equibles transcripts", (
        "company.equibles.transcripts",
    )),
    TimerBinding("quant-data-current-news-refresh.timer", "Current news", (
        "news.fmp.stock_latest_current_evidence",
        "news.current_multi_source_evidence",
    )),
)

_PROPERTIES = "Id,LoadState,ActiveState,SubState,TimersCalendar,NextElapseUSecRealtime"
_CALENDAR = re.compile(r"^\{ OnCalendar=(.+?) ; next_elapse=.* \}$")
_WEEKDAY = re.compile(r"^Mon\.\.Fri \*-\*-\* (\d{2}:\d{2}):00 America/New_York$")


def _timer_properties(output: str) -> dict[str, dict[str, list[str]]]:
    result = {}
    allowed = {binding.unit for binding in TIMER_BINDINGS}
    for block in output.strip().split("\n\n"):
        properties: dict[str, list[str]] = {}
        for line in block.splitlines():
            key, separator, value = line.partition("=")
            if separator:
                properties.setdefault(key, []).append(value)
        ids = properties.get("Id", [])
        if len(ids) == 1 and ids[0] in allowed:
            result[ids[0]] = properties
    return result


def _property(properties: Mapping[str, list[str]], name: str) -> str:
    values = properties.get(name, [])
    return values[0] if len(values) == 1 else ""


def _cadence(properties: Mapping[str, list[str]]) -> str:
    calendars = []
    for value in properties.get("TimersCalendar", []):
        match = _CALENDAR.fullmatch(value)
        if match is None:
            continue
        calendar = match[1]
        weekday = _WEEKDAY.fullmatch(calendar)
        if weekday:
            label = f"Weekdays at {weekday[1]} New York"
        elif calendar == "*-*-* 00:10:00 UTC":
            label = "Daily at 00:10 UTC"
        elif calendar == "*-*-* *:10:00 UTC":
            label = "Hourly at :10 UTC"
        elif calendar == "Fri *-*-01..07 10:05:00 America/New_York":
            label = "First Friday of each month at 10:05 New York"
        else:
            label = calendar[:200]
        calendars.append(label)
    return "; ".join(sorted(set(calendars))) or "Unavailable"


def _next_trigger(value: str, now: datetime) -> str | None:
    try:
        instant = datetime.strptime(value, "%a %Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    # systemd may retain a past elapse while a service is running. It is not a
    # future fetch and must never be presented as one.
    if instant <= now:
        return None
    return instant.isoformat().replace("+00:00", "Z")


def _unit_schedule(properties: Mapping[str, list[str]], now: datetime) -> dict[str, str | None]:
    cadence = _cadence(properties)
    loaded = _property(properties, "LoadState")
    active = _property(properties, "ActiveState")
    if loaded == "not-found" or (loaded == "loaded" and active in {"inactive", "failed"}):
        return dict(refresh_cadence=cadence, next_scheduled_fetch=None,
                    schedule_state="inactive", schedule_note="Local timer is not active.")
    if loaded != "loaded" or active != "active":
        return dict(refresh_cadence=cadence, next_scheduled_fetch=None,
                    schedule_state="unavailable", schedule_note="Local timer status is unavailable.")
    upcoming = _next_trigger(_property(properties, "NextElapseUSecRealtime"), now)
    return dict(refresh_cadence=cadence, next_scheduled_fetch=upcoming,
                schedule_state="scheduled" if upcoming else "unavailable",
                schedule_note="Scheduled batch start." if upcoming else "Next trigger has not been announced.")


def read_local_refresh_schedules(registry: Registry) -> dict[str, dict[str, str | None]]:
    """Read one bounded local timer snapshot; never start or change a unit.

    Called only by the production HTML page. The retained-only public tool and
    JSON endpoint never invoke this reader. Failed reads return safe labels.
    """

    properties: dict[str, dict[str, list[str]]] = {}
    command = ["/usr/bin/systemctl", "--user", "show", "--no-pager", "--all",
               f"--property={_PROPERTIES}", *(binding.unit for binding in TIMER_BINDINGS)]
    try:
        response = subprocess.run(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
            timeout=3, check=False, shell=False,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "TZ": "UTC",
                 "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}"},
        )
        if response.returncode == 0 and len(response.stdout) <= 65_536:
            properties = _timer_properties(response.stdout)
    except (OSError, subprocess.TimeoutExpired):
        pass
    now = datetime.now(timezone.utc)
    units = {binding.unit: _unit_schedule(properties.get(binding.unit, {}), now)
             for binding in TIMER_BINDINGS}
    result: dict[str, dict[str, str | None]] = {}
    for target in registry_data_status_targets(registry):
        bindings = [binding for binding in TIMER_BINDINGS if target.dataset_id in binding.datasets]
        if not bindings:
            result[target.id] = dict(
                refresh_cadence="No recurring schedule", next_scheduled_fetch=None,
                schedule_state="unmapped", schedule_note="No mapped recurring timer.",
            )
            continue
        schedules = [units[binding.unit] for binding in bindings]
        upcoming = sorted(str(item["next_scheduled_fetch"]) for item in schedules
                          if item["next_scheduled_fetch"] is not None)
        state = ("scheduled" if upcoming else "unavailable"
                 if any(item["schedule_state"] == "unavailable" for item in schedules)
                 else "inactive")
        if len(bindings) == 1:
            result[target.id] = dict(schedules[0])
            continue
        cadence = "; ".join(f"{binding.label}: {item['refresh_cadence']}"
                            for binding, item in zip(bindings, schedules))
        note = "Earliest known batch start across mapped timers."
        if any(item["schedule_state"] != "scheduled" for item in schedules):
            note += " Some mapped timers are inactive or unavailable."
        result[target.id] = dict(refresh_cadence=cadence,
                                next_scheduled_fetch=upcoming[0] if upcoming else None,
                                schedule_state=state, schedule_note=note)
    return result


__all__ = ("read_local_refresh_schedules",)
