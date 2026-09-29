"""Continue the selected raw-transcript history within the approved daily cap.

The checkpoint's paid entitlement ceiling remains independent of this narrower
operating allocation. Every request still uses the original shared job ledger.
"""
from datetime import datetime, timedelta, timezone
import json
import time

from ..errors import ConflictError
from . import equibles_transcript_backfill as job
from .equibles_catalogue import continuation_decision
from .equibles_paid_policy import validate_checkpoint, operating_daily_cap

DAILY_CEILING = 10000
MAX_INVOCATIONS = 24
MAX_SECONDS = 6 * 3600
MAX_BYTES = 4 * 1024 * 1024 * 1024


class DailyCappedTransport:
    def __init__(self, root, transport, day, clock, deadline, monotonic):
        self.root, self.transport, self.day, self.clock = root, transport, day, clock
        self.deadline, self.monotonic = deadline, monotonic

    def request(self, path):
        # The caller holds the original job lock and has already reserved this GET.
        state = json.loads(job.read_file(self.root / "state.json", 32 * 1024 * 1024))
        charged = state["usage"].get(self.day, {}).get("attempted", 0)
        if self.clock().astimezone(timezone.utc).date().isoformat() != self.day:
            raise ConflictError("Equibles daily allocation crossed its UTC day")
        if not 1 <= charged <= operating_daily_cap(state, DAILY_CEILING):
            raise ConflictError("Equibles dispatch exceeds the approved daily allocation")
        if self.monotonic() > self.deadline - job.REQUEST_TIMEOUT_SECONDS:
            raise ConflictError("Equibles allocation expired before dispatch; charge retained")
        return self.transport.request(path)


def run_day(root, publisher, transport, *, clock=job.now, monotonic=time.monotonic,
            sleeper=time.sleep, expected_day=None, parallel=False,
            max_seconds=MAX_SECONDS, max_bytes=MAX_BYTES, max_invocations=MAX_INVOCATIONS):
    for value,cap in ((max_seconds,MAX_SECONDS),(max_bytes,MAX_BYTES),(max_invocations,MAX_INVOCATIONS)):
        if type(value) is not int or not 1 <= value <= cap:
            raise ConflictError("Equibles allocation exceeds its approved bounds")
    from .equibles_parallel_backfill import run_wave
    runner = run_wave if parallel else job.run
    started = monotonic()
    entered = clock().astimezone(timezone.utc)
    day = entered.date().isoformat()
    if expected_day is not None and day != expected_day:
        raise ConflictError("Dated Equibles allocation cannot start on another UTC day")
    midnight = datetime.combine(entered.date() + timedelta(days=1),
                                datetime.min.time(), tzinfo=timezone.utc)
    deadline = started + min(max_seconds, max(0, (midnight - entered).total_seconds()))
    capped = DailyCappedTransport(root, transport, day, clock, deadline, monotonic)
    requests = received = invocations = 0
    last_report = None
    outcome = "allocation_exhausted"
    with job.job_lock(root / "continuations"):
        initial_state = json.loads(job.read_file(root / "state.json", 32 * 1024 * 1024))
        retries_before = initial_state.get("download_retry_charges", {}).get(day, 0)
        def report():
            result = {"contract": "quant_data.equibles_daily_backfill.v1",
                      "day": day, "outcome": outcome, "daily_ceiling": DAILY_CEILING,
                      "requests": requests, "received_bytes": received,
                      "invocations": invocations, "max_invocations": max_invocations,
                      "max_seconds": max_seconds, "max_bytes": max_bytes, "parallel_callers": 4 if parallel else 1,
                      "automatic_retries": 0, "last_report": last_report}
            if (last_report or {}).get("download_failure_policy"):
                result["download_failure_policy"] = last_report["download_failure_policy"]
                result["automatic_retries"] = max(0, last_report["download_retry_charges_today"] - retries_before)
                result["unresolved_quarters"] = last_report["unresolved_quarters"]
                result["unresolved_catalogues"] = last_report["unresolved_catalogues"]
            job.atomic(root / "daily-status.json", result, replace=True)
            return result

        report()
        for _ in range(max_invocations):
            if clock().astimezone(timezone.utc).date().isoformat() != day:
                outcome = "utc_day_finished"
                break
            seconds = int(deadline - monotonic())
            if seconds <= 35 or max_bytes - received < job.MAX_BYTES:
                break
            with job.job_lock(root):
                state = json.loads(job.read_file(root / "state.json", 32 * 1024 * 1024))
                validate_checkpoint(state)
                if state["version"] != 2:
                    raise ConflictError("Selected daily backfill requires its converted checkpoint")
                bucket = state["usage"].get(day, {})
                allowance = min(operating_daily_cap(state, DAILY_CEILING) - bucket.get("attempted", 0),
                                DAILY_CEILING - requests)
                if allowance <= 0:
                    outcome = "daily_cap"
                    break
                if bucket.get("paid_headers_verified"):
                    allowance = min(allowance, bucket["remaining"])
                    if allowance <= 0:
                        outcome = "provider_quota"
                        break
            last_report = runner(root, publisher, capped, clock=clock,
                monotonic=monotonic, sleeper=sleeper, allow_paid=True,
                max_requests=min(1000, allowance),
                max_run_seconds=min(3600, seconds),
                max_total_bytes=min(job.MAX_TOTAL_BYTES, max_bytes - received),
                deadline=deadline)
            invocations += 1
            requests += last_report["requests_this_run"]
            received += last_report["response_bytes_this_run"]
            outcome = last_report["outcome"]
            report()
            decision = continuation_decision(last_report, observed_at=clock().isoformat())
            if decision["action"] != "continue":
                break
            if last_report["requests_this_run"] == 0:
                outcome = "no_new_work_progress"
                break
            outcome = "allocation_exhausted"
        return report()
