"""Sharadar-only recovery: immutable attempt links and two timed retry slots."""
from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ..errors import ConflictError
from .collection_queue import _read, _response, _clock, _retry_at, atomic
from .equibles_transcript_backfill import private_directory

CONTRACT = "quant_data.sharadar_retry.v1"
ZONE = ZoneInfo("America/New_York")


def instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class SharadarRecovery:
    """Used only under the refresh-supervisor lock; never changes old evidence."""
    def __init__(self, root, *, utcnow, monotonic, sleeper, deadline):
        self.root, self.utcnow = root, utcnow
        self.monotonic, self.sleeper, self.deadline = monotonic, sleeper, deadline
        self.path = root / "refresh-retry-state.json"
        self.last_failure = None
        self.next_retry_at = None
        now = _clock(utcnow)
        day = instant(now).astimezone(ZONE).date().isoformat()
        self.state = _read(self.path) or {
            "contract": CONTRACT, "day": day, "first_failure_at": None,
            "slots": [], "selections": {}}
        state = self.state
        if (set(state) != {"contract", "day", "first_failure_at", "slots", "selections"}
                or state["contract"] != CONTRACT or state["day"] > day
                or not isinstance(state["slots"], list) or len(state["slots"]) > 3
                or not isinstance(state["selections"], dict) or len(state["selections"]) > 200000):
            raise ConflictError("Sharadar retry state differs")
        if state["day"] != day:
            state.update(day=day, first_failure_at=None, slots=[])
        slots = [item["slot"] for item in state["slots"]]
        if slots not in ([], [0], [1], [0, 1], [1, 2], [0, 1, 2]):
            raise ConflictError("Sharadar retry slots differ")
        for item in state["slots"]:
            if (set(item) != {"slot", "at", "unit_id"}
                    or instant(item["at"]).astimezone(ZONE).date().isoformat() != day):
                raise ConflictError("Sharadar retry day differs")
        if state["first_failure_at"] is not None:
            instant(state["first_failure_at"])

    def save(self):
        atomic(self.path, self.state, replace=True)

    def unit(self, original):
        stamp = self.state["selections"].get(original.unit_id)
        if stamp is None:
            return original
        selected = replace(original, observation_window=stamp)
        link = _read(self.root / "retry-links" / (selected.unit_id + ".json"))
        if (not isinstance(link, dict) or link.get("original_unit_id") != original.unit_id
                or link.get("retry_unit_id") != selected.unit_id
                or link.get("request_id") != original.request_id
                or link.get("retry_at") != stamp):
            raise ConflictError("Sharadar retry evidence link differs")
        return selected

    def response(self, original):
        selected = self.unit(original)
        evidence = _response(self.root, selected.unit_id, selected)
        if evidence is not None and evidence[0]["status"] != 200:
            self.last_failure = (original, selected, evidence[0])
        return evidence

    def retry(self):
        if self.last_failure is None:
            return "not_retryable"
        original, previous, receipt = self.last_failure
        if not 500 <= receipt["status"] <= 599:
            return "not_retryable"
        state = self.state
        now = _clock(self.utcnow)
        if instant(now).astimezone(ZONE).date().isoformat() != state["day"]:
            return "retry_next_day"  # Never acquire another day's budget mid-run.
        failure_day = instant(receipt["captured_at"]).astimezone(ZONE).date().isoformat()
        used = [item["slot"] for item in state["slots"]]
        if failure_day < state["day"] and not used:
            slot, due = 0, instant(now)
        else:
            if state["first_failure_at"] is None:
                state["first_failure_at"] = receipt["captured_at"]
                self.save()
            slot = 2 if 1 in used else 1
            if 2 in used:
                return "retry_next_day"
            due = instant(state["first_failure_at"]) + timedelta(seconds=600 if slot == 1 else 1800)
            if slot == 2:
                first_retry = next(item for item in state["slots"] if item["slot"] == 1)
                # A late first retry must not make the second retry immediate.
                due = max(due, instant(first_retry["at"]) + timedelta(seconds=1200))
        if receipt.get("headers", {}).get("retry-after") is not None:
            due = max(due, instant(_retry_at(receipt)))
        self.next_retry_at = due.isoformat()
        delay = max(0, (due - instant(now)).total_seconds())
        if self.monotonic() + delay + original.timeout_seconds >= self.deadline:
            return "invocation_budget"
        wait_until = self.monotonic() + delay
        while self.monotonic() < wait_until:
            self.sleeper(min(30, wait_until - self.monotonic()))
        at = _clock(self.utcnow)
        if (instant(at) < due
                or instant(at).astimezone(ZONE).date().isoformat() != state["day"]
                or self.monotonic() + original.timeout_seconds >= self.deadline):
            return "invocation_budget"
        selected = replace(original, observation_window=at)
        if selected.unit_id == previous.unit_id:
            raise ConflictError("Sharadar retry must have a fresh attempt identity")
        private_directory(self.root / "retry-links")
        atomic(self.root / "retry-links" / (selected.unit_id + ".json"), {
            "contract": CONTRACT, "original_unit_id": original.unit_id,
            "retry_unit_id": selected.unit_id, "request_id": original.request_id,
            "previous_unit_id": previous.unit_id,
            "previous_content_sha256": receipt["content_sha256"],
            "retry_at": at, "day": state["day"], "slot": slot})
        state["selections"][original.unit_id] = at
        state["slots"].append({"slot": slot, "at": at, "unit_id": original.unit_id})
        self.save()  # Durable selection precedes the existing queue's charge/GET.
        self.next_retry_at = None
        return "retry"

    def details(self, result):
        failed = self.last_failure
        params = dict(failed[0].parameters) if failed else {}
        return {
            "outcome": result["outcome"],
            "http_status": failed[2]["status"] if failed else None,
            "dimension": params.get("dimension"),
            "symbol_count": len(failed[0].selected_symbols) if failed else 0,
            "completed_partitions": result.get("completed_partitions", 0),
            "total_partitions": result.get("total_partitions", 0),
            "requests": result.get("requests", 0),
            "retries_today": sum(item["slot"] > 0 for item in self.state["slots"]),
            "next_retry_at": self.next_retry_at,
            "recovery": "next_day" if result["outcome"] == "retry_next_day" else
                "budget" if result["outcome"] in ("invocation_budget", "daily_budget") else
                "complete" if result["outcome"] in ("succeeded", "already_complete") else "blocked"}
