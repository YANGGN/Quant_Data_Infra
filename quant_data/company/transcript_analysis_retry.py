"""Opt-in, bounded retries for transient Codex transcript failures."""
from __future__ import annotations

import hashlib
import json
import random
import threading
import time
from pathlib import Path

from .transcript_analysis_codex import CodexTranscriptTransport, MAX_STREAM_BYTES
from ..errors import ConflictError, ResourceLimitError, ValidationError

POLICY = "transcript_transient_retry.v1"
DELAYS = (30, 120)


class AttemptBudgetExhausted(ResourceLimitError):
    pass


class AttemptBudget:
    """One coordinator owns this durable budget while holding its batch job lock."""

    def __init__(self, path, *, plan_id, limit, initial_calls):
        if (type(limit) is not int or type(initial_calls) is not int
                or not 0 <= initial_calls <= limit):
            raise ValidationError("Invalid transcript attempt budget")
        self.path = Path(path)
        self.lock = threading.Lock()
        if self.path.exists():
            self.state = json.loads(self.path.read_text())
            s = self.state
            if (set(s) != {"contract", "plan_id", "limit", "initial_calls", "used", "retries"}
                    or s["contract"] != POLICY or s["plan_id"] != plan_id or s["limit"] != limit
                    or any(type(s[k]) is not int for k in ("initial_calls", "used", "retries"))
                    or not 0 <= s["initial_calls"] <= initial_calls <= s["used"] <= limit
                    or not 0 <= s["retries"] <= s["used"] - s["initial_calls"]):
                raise ConflictError("Retained transcript attempt budget differs")
        else:
            self.state = dict(contract=POLICY, plan_id=plan_id, limit=limit,
                              initial_calls=initial_calls, used=initial_calls, retries=0)
            self._save()

    def _save(self):
        from ..operations.equibles_transcript_backfill import atomic
        atomic(self.path, self.state, replace=True)

    def reserve(self, *, retry):
        with self.lock:
            if self.state["used"] >= self.state["limit"]:
                raise AttemptBudgetExhausted("Approved transcript model-call budget exhausted")
            self.state["used"] += 1
            self.state["retries"] += int(retry)
            self._save()  # Charge before launching; an interrupted claim is never refunded.


def transient_failure(root):
    """Only classify retained, finished native attempts; unknown failures stay terminal."""
    root = Path(root)
    try:
        finished = json.loads((root / "finished.json").read_text())
        raw = (root / "stdout.jsonl").read_bytes()
        if len(raw) > MAX_STREAM_BYTES:
            return False
        events = [json.loads(line) for line in raw.splitlines() if line.strip()]
    except (OSError, ValueError):
        return False
    if not isinstance(finished, dict) or any(not isinstance(e, dict) or not isinstance(e.get("type"), str) for e in events):
        return False
    if any(e.get("type") == "turn.completed" for e in events):
        return False
    # Completed output or an unexpected tool/item is a validation issue, not a retry signal.
    for e in events:
        if e.get("type", "").startswith("item."):
            item = e.get("item", {})
            if not isinstance(item, dict) or item.get("type") != "reasoning":
                return False
    if finished.get("failure") not in (None, "timeout"):
        return False
    messages = []
    for e in events:
        if e.get("type") == "error":
            messages.append(e.get("message"))
        elif e.get("type") == "turn.failed":
            error = e.get("error")
            messages.append(error.get("message") if isinstance(error, dict) else None)
    if any(not isinstance(m, str) for m in messages):
        return False
    temporary = ("selected model is at capacity", "429", "too many requests",
                 "rate limit", "rate_limit", "stream disconnected before completion",
                 "connection reset", "connection timed out", "request timed out", "service unavailable",
                 "502 bad gateway", "503 service", "504 gateway")
    permanent = ("unauthorized", "authentication", "invalid api key", "permission denied",
                 "usage limit", "quota exceeded", "insufficient_quota", "context length",
                 "unsupported model", "invalid request")
    if any(any(t in m.lower() for t in permanent) for m in messages):
        return False
    if finished.get("failure") == "timeout":
        return True
    return bool(messages) and all(any(t in m.lower() for t in temporary) for m in messages)


class RetryingCodexTransport:
    """At most three native calls for one new request; default adapter is unchanged."""

    backend = CodexTranscriptTransport.backend

    def __init__(self, transport, budget, *, sleep=time.sleep, jitter=None):
        if not isinstance(transport, CodexTranscriptTransport):
            raise ValidationError("Retry policy requires the existing Codex subscription adapter")
        self.transport, self.budget = transport, budget
        self.sleep = sleep
        self.jitter = jitter or (lambda: random.uniform(0, 5))
        self.attempt_count = 0
        self.retryable_failure = False

    def _deadline_seconds(self):
        return self.transport._deadline_seconds()

    def prepare_credentials(self):
        self.transport.prepare_credentials()

    def request(self, raw):
        identifier = hashlib.sha256(raw).hexdigest()
        original = self.transport.evidence_root / identifier
        if (original / "started.json").exists():
            raise ConflictError("A prior native attempt cannot enter the new-request retry policy")
        transport = self.transport
        for number in range(1, 4):
            self.budget.reserve(retry=number > 1)
            self.attempt_count += 1
            try:
                return transport.request(raw)
            except (ValidationError, ResourceLimitError):
                evidence = transport.evidence_root / identifier
                self.retryable_failure = transient_failure(evidence)
                if number == 3 or not self.retryable_failure:
                    raise
                delay = DELAYS[number - 1] + min(5, max(0, self.jitter()))
                from ..operations.equibles_transcript_backfill import atomic, private_directory
                retries = original / "retries"
                private_directory(retries)
                atomic(retries / ("retry-" + str(number) + ".json"),
                       dict(contract=POLICY, request_sha256=identifier,
                            completed_attempt=number, next_attempt=number + 1, delay_seconds=delay))
                self.sleep(delay)
                transport = CodexTranscriptTransport(
                    binary=self.transport.binary,
                    evidence_root=retries / ("attempt-" + str(number + 1)),
                    environment=self.transport.environment,
                    timeout_seconds=self.transport.timeout_seconds)
                transport._ready = self.transport._ready
