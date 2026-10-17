"""Durable, opt-in one-retry policy for the finite Equibles history walk."""
import copy
from . import equibles_transcript_backfill as job
from .equibles_catalogue import catalogue_identifier
from ..errors import ConflictError

POLICY = {"version": 1, "max_download_retries": 1,
          "on_exhaustion": "record_unresolved_continue"}
RETRYABLE = frozenset(("timeout", "connection_error", "incomplete_response",
                      "worker_lost", "uncertain_attempt", "http_failure"))
RETRYABLE_HTTP = frozenset((408, 500, 502, 503, 504))


def enabled(state):
    value = state.get("download_failure_policy")
    if value is None:
        return False
    if value != POLICY or state.get("version") != 2:
        raise ConflictError("Equibles download failure policy differs")
    return True


def record(state, path):
    if not enabled(state):
        return None
    value = state.get("download_failures", {}).get(catalogue_identifier(state, path))
    if value is not None and value["path"] != path:
        raise ConflictError("Equibles download failure path differs")
    return value


def unresolved(state, path):
    value = record(state, path)
    return value if value is not None and value["outcome"] == "unresolved" else None


def retry_of(state, path):
    value = record(state, path)
    if value is None or value["outcome"] == "recovered":
        return None
    if value["outcome"] != "retry_pending" or len(value["failures"]) != 1:
        raise ConflictError("Equibles exhausted download cannot be requested again")
    return value["failures"][0]["reservation"]["attempt"]


def fail(state, entry, diagnostic, *, observed_at):
    """Idempotent settlement only; every new GET is reserved by the existing caller."""
    if not enabled(state) or diagnostic["category"] not in RETRYABLE:
        return False
    if diagnostic["category"] == "http_failure" and diagnostic.get("http_status") not in RETRYABLE_HTTP:
        return False
    key = catalogue_identifier(state, entry["path"])
    records = state.setdefault("download_failures", {})
    value = records.setdefault(key, {"path": entry["path"], "failures": [], "outcome": "retry_pending"})
    if value["path"] != entry["path"]:
        raise ConflictError("Equibles failed download identity differs")
    prior = next((f for f in value["failures"] if f["reservation"]["attempt"] == entry["attempt"]), None)
    if prior is not None:
        if prior["reservation"] != entry or prior["diagnostic"] != diagnostic:
            raise ConflictError("Equibles failed attempt changed during replay")
        return True
    if len(value["failures"]) >= 2:
        raise ConflictError("Equibles failed download exceeded one retry")
    if value["failures"] and entry.get("retry_of") != value["failures"][0]["reservation"]["attempt"]:
        raise ConflictError("Equibles retry has no matching original attempt")
    value["failures"].append({"reservation": copy.deepcopy(entry),
                              "diagnostic": copy.deepcopy(diagnostic),
                              "observed_at": job.utc(observed_at)})
    value["outcome"] = "unresolved" if len(value["failures"]) == 2 else "retry_pending"
    return True


def succeeded(state, entry):
    value = record(state, entry["path"])
    if value is not None:
        value["outcome"] = "recovered"
        value["response_attempt"] = entry["attempt"]


def reserve_retry(state, entry, day):
    original = retry_of(state, entry["path"])
    if original is not None:
        entry["retry_of"] = original
        counts = state.setdefault("download_retry_charges", {})
        counts[day] = counts.get(day, 0) + 1


def evidence(value):
    return {"path": value["path"], "reason": "download_unresolved_after_retry",
            "attempts": [copy.deepcopy(f["reservation"]) for f in value["failures"]],
            "failures": [copy.deepcopy(f["diagnostic"]) for f in value["failures"]],
            "observed_at": value["failures"][-1]["observed_at"]}


def stats(state, day):
    if not enabled(state):
        return {}
    records = list(state["completed"].values()) + [state.get("current") or {}]
    return {"download_failure_policy": dict(POLICY),
            "download_retry_charges_today": state.get("download_retry_charges", {}).get(day, 0),
            "unresolved_quarters": sum(len(r.get("unresolved_transcripts", [])) for r in records),
            "unresolved_catalogues": sum(bool(r.get("unresolved_catalogue")) for r in records)}
