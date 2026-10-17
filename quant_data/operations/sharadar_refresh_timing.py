"""Sharadar-only clock recovery and invocation-wide request pacing."""
from contextlib import contextmanager
from datetime import datetime
import sys
import time

from ..errors import ResourceLimitError
from ..json_codec import dumps_strict
from ..market.collection_universe import _utc
from .collection_queue import BoundedForwardClock, _read, _state


MAX_CLOCK_WAIT_SECONDS = 5.0


class SharadarClockRecoveryError(ResourceLimitError):
    """A clock adjustment did not settle inside its original time allowance."""


class SharadarRefreshTiming:
    def __init__(self, clock, fetch, *, deadline, interval, monotonic=time.monotonic,
                 sleeper=time.sleep):
        self.monotonic = monotonic
        self.sleeper = sleeper
        self.deadline = deadline
        self.interval = interval
        self.transport = fetch
        self.last_dispatch = None
        self.forward = BoundedForwardClock(
            clock, deadline=deadline, monotonic=monotonic, sleeper=sleeper)

    def observe(self, timestamp):
        """Remember original evidence time without changing that evidence."""
        if timestamp is not None:
            value = datetime.fromisoformat(_utc(timestamp).replace("Z", "+00:00"))
            if self.forward.last is None or value > self.forward.last:
                self.forward.last = value

    def seed(self, root):
        ledger = _state(root, "sharadar")
        last = ledger["last_request_at"]
        self.observe(last)
        if last is None:
            return
        # Monotonic readings cannot be carried across host restarts. Wait at
        # least one interval in this process, plus the queue's durable UTC check.
        self.last_dispatch = self.monotonic()
        day = last[:10]
        count = ledger["usage"][day]["charged_attempts"]
        attempt = _read(root / "attempts" / (day + "-" + str(count) + ".json"))
        if attempt is not None and attempt["charged_at"] == last:
            receipt = _read(root / "responses" / (attempt["unit_id"] + ".json"))
            if receipt is not None and receipt["request_id"] == attempt["request_id"]:
                self.observe(receipt["captured_at"])

    @contextmanager
    def bound(self, deadline):
        previous = self.deadline
        self.deadline = min(previous, deadline)
        try:
            yield
        finally:
            self.deadline = previous

    def _event(self, outcome, started):
        print(dumps_strict({
            "event": "sharadar_clock_recovery",
            "outcome": outcome,
            "waited_seconds": round(max(0, self.monotonic() - started), 6),
            "required_at": self.forward.last.isoformat() if self.forward.last else None,
        }), file=sys.stderr)

    def __call__(self):
        started = self.monotonic()
        self.forward.deadline = min(self.deadline, started + MAX_CLOCK_WAIT_SECONDS)
        waiting = False

        def wait(seconds):
            nonlocal waiting
            if not waiting:
                self._event("waiting", started)
                waiting = True
            self.sleeper(seconds)

        self.forward.sleeper = wait
        try:
            value = self.forward()
            if waiting and self.monotonic() >= self.forward.deadline:
                raise ResourceLimitError("Clock recovery deadline reached")
        except ResourceLimitError:
            self._event("exhausted", started)
            raise SharadarClockRecoveryError(
                "Sharadar clock recovery exceeded five seconds or the original deadline") from None
        if waiting:
            self._event("recovered", started)
        return value

    def before_request(self, *, unit, deadline):
        """Pace before a durable charge; never sleep inside the HTTP attempt."""
        deadline = min(self.deadline, deadline)
        while True:
            now = self.monotonic()
            delay = 0 if self.last_dispatch is None else max(
                0, self.last_dispatch + self.interval - now)
            if now + delay + unit.timeout_seconds >= deadline:
                return False
            if delay <= 0:
                return True
            self.sleeper(delay)

    def fetch(self, **kwargs):
        self.last_dispatch = self.monotonic()
        response = self.transport(**kwargs)
        # Return immediately so the queue retains the original response before
        # the next clock sample can wait or fail.
        self.observe(response.captured_at)
        return response
