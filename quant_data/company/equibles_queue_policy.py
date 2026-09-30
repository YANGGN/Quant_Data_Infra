"""Pure saved-queue policy shared by Equibles collection and read-only planning.

No transport, credentials, stores, filesystem access or scheduler execution.
Callers retain their existing state validation and date parsing boundaries.
"""
from datetime import date
from zoneinfo import ZoneInfo

CONTRACT = "quant_data.equibles_incremental.v1"
DAILY_CAP = 100
TZ = ZoneInfo("America/Toronto")


def lane_quotas(today):
    return (dict(recent=80, delayed=15, fallback=5) if today.weekday() < 5
            else dict(recent=0, delayed=0, fallback=100))


def next_task(state, today, spent, quotas, *, parse_day=date.fromisoformat):
    weekend = today.weekday() >= 5
    candidates = []
    for symbol, task in state["tasks"].items():
        if task.get("blocked") or parse_day(task["due"]) > today:
            continue
        partial = task["phase"] in ("download", "publish")
        lane = "fallback" if weekend else task["lane"]
        if weekend and task["lane"] != "fallback" and not partial and task["lane"] != "delayed":
            continue
        priority = (0 if partial else 1, state["watch"].get(symbol, {}).get("last_checked", ""),
                    task["queued_at"], symbol)
        candidates.append((lane, priority, symbol))
    if not candidates:
        return None
    protected = [x for x in candidates if spent[x[0]] < quotas[x[0]]]
    partials = [x for x in candidates if x[1][0] == 0]
    choices = partials or protected or candidates  # complete retained work before discovery
    lane, _, symbol = min(choices, key=lambda x:x[1])
    return symbol, lane
