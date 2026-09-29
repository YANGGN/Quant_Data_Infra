"""Bounded delayed continuation of an explicitly selected transcript batch."""
from __future__ import annotations

import time

from .equibles_transcript_backfill import atomic, job_lock, private_directory
from .transcript_universe_batch import load
from ..errors import ConflictError, ValidationError

POLICY = "transcript_delayed_resume.v1"
LOCK_TIMEOUT = "Timed out acquiring the physical store lock"


def _records(directory):
    return {p.stem: load(p) for p in (directory / "records").glob("*.json")}


def _restartable(result, records, previous):
    status = result["status"]
    if status == "paused_lock":
        return result.get("reason") == LOCK_TIMEOUT
    if status in ("paused_input", "paused_publication"):
        blockers = [r for r in records.values()
                    if r.get("stop_new_calls") and r["status"] in
                    ("input_failed", "publication_failed")]
        return bool(blockers) and all(
            r.get("error_type") == "ConflictError" and r.get("reason") == LOCK_TIMEOUT
            for r in blockers)
    if status == "paused_after_transport_failures":
        failures = [r for key, r in records.items()
                    if key not in previous and r.get("transport_failed")]
        return bool(failures) and all(r.get("retryable_transport_failure") is True
                                      for r in failures)
    return False


def supervise(job, identifier, transport_factory, *, concurrency=20, retry_policy=None,
              max_restarts=10, restart_delay_seconds=180, on_progress=None,
              sleep=time.sleep, now=time.time):
    """Drain before waiting; preserve all attempt/restart budgets across process restarts."""
    if (type(max_restarts) is not int or not 0 <= max_restarts <= 10
            or type(restart_delay_seconds) is not int or not 1 <= restart_delay_seconds <= 600
            or type(concurrency) is not int or not 1 <= concurrency <= 100):
        raise ValidationError("Invalid bounded transcript restart policy")
    job.plan(identifier)
    directory = job.root / "runs" / identifier
    private_directory(directory)
    private_directory(directory / "supervisor")
    path = directory / "supervisor" / "state.json"
    config = dict(contract=POLICY, plan_id=identifier, max_restarts=max_restarts,
                  restart_delay_seconds=restart_delay_seconds, concurrency=concurrency,
                  retry_policy=retry_policy, lock_timeout_seconds=job.lock_timeout_seconds)
    # This separate lock prevents competing supervisors without holding the store/batch
    # locks during backoff. execute() retains ownership of its ordinary batch locks.
    with job_lock(directory / "supervisor"):
        if path.exists():
            state = load(path)
            if (state.get("configuration") != config
                    or type(state.get("restarts_used")) is not int
                    or not 0 <= state["restarts_used"] <= max_restarts
                    or type(state.get("pending_restart")) is not bool):
                raise ConflictError("Retained transcript restart policy differs")
        else:
            state = dict(configuration=config, restarts_used=0, pending_restart=False)
            atomic(path, state)
        while True:
            if state["pending_restart"]:
                delay = max(0, float(state["restart_not_before"]) - now())
                if delay:
                    sleep(delay)
                state["pending_restart"] = False
                atomic(path, state, replace=True)
            previous = set(_records(directory))
            try:
                result = job.execute(identifier, transport_factory, concurrency=concurrency,
                                     retry_policy=retry_policy, on_progress=on_progress)
            except ConflictError as exc:
                if str(exc) != LOCK_TIMEOUT:
                    raise
                result = dict(job.status(identifier), status="paused_lock",
                              reason=LOCK_TIMEOUT, in_flight=0)
            state["last_result"] = result
            allowed = _restartable(result, _records(directory), previous)
            if not allowed or state["restarts_used"] >= max_restarts:
                state["status"] = ("restart_limit_reached" if allowed else result["status"])
                atomic(path, state, replace=True)
                return dict(result, supervisor_status=state["status"],
                            automatic_restarts=state["restarts_used"])
            # Reserve before sleeping so interruption cannot reset the restart allowance.
            state.update(restarts_used=state["restarts_used"] + 1,
                         pending_restart=True, restart_not_before=now() + restart_delay_seconds,
                         status="waiting_to_resume")
            atomic(path, state, replace=True)
            if on_progress:
                on_progress(dict(result, status="waiting_to_resume", in_flight=0,
                                 automatic_restarts=state["restarts_used"],
                                 restart_not_before=state["restart_not_before"]))
