"""Cross-process admission for the singleton monitor and finite baseline owners.

Live cycles take exclusive provider ownership to avoid overlapping Theta logins.
Backfill has at most four workers at the existing request gate, and one during
weekdays 08:55-16:15 ET. These locks never protect a SQLite transaction.
"""
from contextlib import contextmanager, ExitStack
from pathlib import Path
from datetime import datetime, timedelta, time as daytime_clock
import fcntl, json, os, re, time, uuid
from .policy import NY


def daytime(now):
    local = now.astimezone(NY)
    return local.weekday() < 5 and 535 <= local.hour * 60 + local.minute < 975


class BackgroundBusy(RuntimeError):
    pass


class ThetaAdmission:
    def __init__(self, root):
        self.directory = Path(root).resolve(strict=True) / "data/.operations/options-monitor"

    def _pause_value(self, now, run_id):
        if now.tzinfo is None:
            raise ValueError("monitor_clock")
        if not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{24}", run_id):
            raise ValueError("monitor_pause_run_id")
        local_day = now.astimezone(NY).date()
        until = datetime.combine(local_day + timedelta(days=1), daytime_clock.min, NY)
        return {"version": 1, "session": local_day.isoformat(), "until": until.isoformat(),
                "backfill_run_id": run_id, "reason": "backfill_priority"}

    def pause_status(self, now):
        if now.tzinfo is None:
            raise ValueError("monitor_clock")
        path = self.directory / ("live-pause-" + now.astimezone(NY).date().isoformat() + ".json")
        try:
            text = path.read_text()
        except FileNotFoundError:
            return None
        if len(text) > 1024:
            raise ValueError("monitor_pause_invalid")
        value = json.loads(text)
        if not isinstance(value, dict) or type(value.get("version")) is not int:
            raise ValueError("monitor_pause_invalid")
        if value != self._pause_value(now, value.get("backfill_run_id")):
            raise ValueError("monitor_pause_invalid")
        return value

    def pause_today(self, now, run_id):
        """Explicit local operation, never called by scheduled collection."""
        value = self._pause_value(now, run_id)
        # The caller first pauses the timer. This owner check drains a clock
        # cycle that started before that pause, without interrupting its calls.
        with self._open("run.lock") as owner:
            if not self._try(owner, fcntl.LOCK_EX):
                raise RuntimeError("monitor_cycle_running")
            path = self.directory / ("live-pause-" + value["session"] + ".json")
            temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
            try:
                with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as out:
                    out.write(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
                    out.flush()
                    os.fsync(out.fileno())
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    if self.pause_status(now) != value:
                        raise ValueError("monitor_pause_conflict")
            finally:
                temporary.unlink(missing_ok=True)
        return value

    def backfill_capacity(self, now, run_id=None):
        pause = self.pause_status(now)
        if pause is not None and pause["backfill_run_id"] == run_id:
            return 4
        return 1 if daytime(now) else 4

    def _open(self, name):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        return os.fdopen(os.open(self.directory / name, os.O_RDWR | os.O_CREAT, 0o600), "a+b")

    @staticmethod
    def _try(handle, mode):
        try:
            fcntl.flock(handle.fileno(), mode | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False

    def live_pending(self):
        # run.lock also recognizes a monitor process loaded before gate rollout.
        for name in ("theta-live-intent.lock", "run.lock"):
            with self._open(name) as handle:
                if not self._try(handle, fcntl.LOCK_SH):
                    return True
        return False

    @contextmanager
    def live(self, *, deadline):
        with ExitStack() as stack:
            intent = stack.enter_context(self._open("theta-live-intent.lock"))
            if not self._try(intent, fcntl.LOCK_EX):
                raise RuntimeError("theta_live_owner_running")
            session = stack.enter_context(self._open("theta-session.lock"))
            while not self._try(session, fcntl.LOCK_EX):
                if time.monotonic() >= deadline:
                    raise RuntimeError("theta_live_admission_deadline")
                time.sleep(min(.05, max(0, deadline - time.monotonic())))
            if time.monotonic() >= deadline:
                raise RuntimeError("theta_live_admission_deadline")
            # A non-secret epoch invalidates every old background auth session,
            # even if a complete live cycle fits between two background calls.
            stamp = self.directory / "theta-session-generation"
            temporary = stamp.with_suffix(".tmp")
            with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as out:
                out.write(uuid.uuid4().hex)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temporary, stamp)
            yield

    @contextmanager
    def background(self, now, *, run_id=None):
        with ExitStack() as stack:
            intent = stack.enter_context(self._open("theta-live-intent.lock"))
            if not self._try(intent, fcntl.LOCK_SH):
                raise BackgroundBusy("live_collection")
            # Do not retain the monitor owner lock: it must admit its clock run.
            with self._open("run.lock") as owner:
                if not self._try(owner, fcntl.LOCK_SH):
                    raise BackgroundBusy("live_collection")
            capacity = stack.enter_context(self._open("theta-background-capacity.lock"))
            mode = fcntl.LOCK_EX if self.backfill_capacity(now, run_id) == 1 else fcntl.LOCK_SH
            if not self._try(capacity, mode):
                raise BackgroundBusy("background_capacity")
            session = stack.enter_context(self._open("theta-session.lock"))
            if not self._try(session, fcntl.LOCK_SH):
                raise BackgroundBusy("live_collection")
            # Live intent may now queue, but its login waits for this request's
            # receipt. New background requests immediately yield to that intent.
            fcntl.flock(intent.fileno(), fcntl.LOCK_UN)
            stamp = self.directory / "theta-session-generation"
            epoch = stamp.read_text() if stamp.exists() else ""
            if epoch and (len(epoch) != 32 or any(c not in "0123456789abcdef" for c in epoch)):
                raise RuntimeError("theta_session_generation_invalid")
            yield epoch
