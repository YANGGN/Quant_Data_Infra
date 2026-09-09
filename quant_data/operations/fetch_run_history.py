"""Durable private run receipts around the nine existing scheduled CLI calls.

This observer does not capture output, fetch data, retry, change exit behavior,
or acquire a canonical-store lock. One private directory belongs to each run.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
import stat
import sys
import time
from typing import Callable, Any
from uuid import uuid4

from ..errors import QuantDataError, ValidationError
from ..json_codec import loads_strict
from .fetch_run_summary import _CAPTURE, validate_counts
from .refresh_status import _ensure_write_root, _replace_receipt, _fsync_directory, _parse_timestamp

BATCH_ARGUMENTS = {
    "quant-data-market-close.timer": (),
    "quant-data-alpaca-spy-options.timer": (),
    "quant-data-macro-current-refresh.timer": (),
    "quant-data-macro-vintages.timer": ("--mode", "refresh"),
    "quant-data-employment-vintages.timer": ("--mode", "refresh"),
    "quant-data-fmp-macro-calendar.timer": (),
    "quant-data-sec-company-fundamentals.timer": (),
    "quant-data-company-market-refresh.timer": (),
    "quant-data-current-news-refresh.timer": (),
    "quant-data-equibles-transcripts.timer": (),
}
DEFAULT_HISTORY_ROOT = Path(__file__).resolve().parents[2] / "data" / ".operations" / "fetch-run-history"
_SCHEMA = "quant_data.fetch_run"
_MAX_BYTES = 4096
_MAX_RUNS = 2048
_MAX_TOTAL_BYTES = 4 * 1024 * 1024
_HEX = re.compile(r"[0-9a-f]{32}\Z")
_BOOT = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
_FIELDS = {"schema", "version", "run_id", "batch_id", "started_at", "finished_at", "outcome", "exit_code", "invocation_id", "pid", "boot_id", "process_start_ticks"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError("Run status time must include an offset")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _process_identity(pid: int) -> tuple[str, str] | None:
    try:
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
        if _BOOT.fullmatch(boot) is None or fields[0] in {"Z", "X"} or not fields[19].isdecimal():
            return None
        return boot, fields[19]
    except (OSError, IndexError):
        return None


def _exit_code(value: object) -> int:
    if value is None:
        return 0
    return int(value) % 256 if isinstance(value, int) else 1


def _warn() -> None:
    # Telemetry failure must neither replace a collector result nor expose errors.
    try:
        sys.stderr.write("[fetch-status] Unable to save run status.\n")
    except Exception:
        pass


def _publish(path: Path, record: dict[str, Any]) -> None:
    from ..json_codec import dumps_strict
    data = dumps_strict(record).encode("utf-8")
    if len(data) > _MAX_BYTES:
        raise ValidationError("Run status is too large")
    _replace_receipt(path, data)


_SUMMARY_BINDING = ("run_id", "batch_id", "started_at", "finished_at", "exit_code")
_SUMMARY_FIELDS = set(_SUMMARY_BINDING) | {"schema", "version", "recorded_at", "provenance", "source_sha256", "counts"}


def save_run_summary(path: Path, record: dict[str, Any], counts: dict[str, Any], *,
                     recorded_at: str, provenance: str = "collector_report",
                     source_sha256: str = "") -> None:
    """Save a separate summary; the original exit receipt is never rewritten."""
    summary = {key: record[key] for key in _SUMMARY_BINDING}
    summary.update(schema="quant_data.fetch_run_summary", version=1, recorded_at=recorded_at,
                   provenance=provenance, source_sha256=source_sha256, counts=validate_counts(counts))
    _validated_summary(summary, record, _parse_timestamp(recorded_at))
    _publish(path, summary)


def _validated_summary(value: object, record: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    if (not isinstance(value, dict) or set(value) != _SUMMARY_FIELDS
            or value["schema"] != "quant_data.fetch_run_summary"
            or type(value["version"]) is not int or value["version"] != 1
            or type(value["exit_code"]) is not int
            or any(value[key] != record[key] for key in _SUMMARY_BINDING)
            or record["outcome"] not in {"succeeded", "failed"}):
        raise ValueError("Invalid run summary binding")
    provenance, digest = value["provenance"], value["source_sha256"]
    if provenance not in {"collector_report", "retained_evidence"} or not isinstance(digest, str):
        raise ValueError("Invalid summary provenance")
    if (provenance == "retained_evidence" and re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or provenance == "collector_report" and digest != ""):
        raise ValueError("Invalid summary evidence")
    recorded = _parse_timestamp(value["recorded_at"])
    if recorded < _parse_timestamp(record["finished_at"]):
        raise ValueError("Summary precedes completion")
    counts = validate_counts(value["counts"])
    if record["exit_code"] == 0 and any(counts[key] for key in ("failed", "partial", "unattempted")):
        raise ValueError("Summary contradicts successful exit")
    return counts if recorded <= now else None


def run_recorded_cli(batch_id: str, operation: Callable[[], Any], *, argv: list[str] | tuple[str, ...],
                     root: Path | None = None, clock: Callable[[], datetime] = _now,
                     environment: dict[str, str] | None = None) -> Any:
    """Invoke the existing CLI once; record only its fixed recurring argument form.

    Imports, --help, rejected arguments and historical/backfill modes do not
    create history. Tests call this helper with an explicit temporary root.
    The original result, exception, stdout and stderr remain the CLI's own.
    """
    if batch_id not in BATCH_ARGUMENTS or tuple(argv) != BATCH_ARGUMENTS[batch_id]:
        return operation()
    record = None
    path = None
    try:
        started = clock()
        started_at = _stamp(started)
        checked_root = _ensure_write_root(DEFAULT_HISTORY_ROOT if root is None else root)
        month = _ensure_write_root(checked_root / started.astimezone(timezone.utc).strftime("%Y-%m"))
        _fsync_directory(checked_root.parent)
        _fsync_directory(checked_root)
        run_id = uuid4().hex
        directory = month / run_id
        directory.mkdir(mode=0o700, exist_ok=False)
        _fsync_directory(month)
        path = directory / "run.json"
        env = os.environ if environment is None else environment
        invocation = env.get("INVOCATION_ID", "")
        if not isinstance(invocation, str) or _HEX.fullmatch(invocation) is None:
            invocation = ""
        identity = _process_identity(os.getpid())
        record = {
            "schema": _SCHEMA, "version": 1, "run_id": run_id, "batch_id": batch_id,
            "started_at": started_at, "finished_at": None, "outcome": "running", "exit_code": None,
            "invocation_id": invocation, "pid": os.getpid(),
            "boot_id": identity[0] if identity else None,
            "process_start_ticks": identity[1] if identity else None,
        }
        _publish(path, record)
    except Exception:
        _warn()

    summaries: list[dict[str, Any]] = []

    def finish(code: int) -> None:
        if record is None or path is None:
            return
        try:
            finished = _stamp(clock())
            if _parse_timestamp(finished) < _parse_timestamp(record["started_at"]):
                raise ValidationError("Run status clock moved backwards")
            completed = {**record, "finished_at": finished, "outcome": "succeeded" if code == 0 else "failed", "exit_code": code}
            _publish(path, completed)
            if summaries:
                save_run_summary(path.with_name("summary.json"), completed, summaries[-1], recorded_at=finished)
        except Exception:
            _warn()

    token = _CAPTURE.set((batch_id, summaries))
    try:
        try:
            result = operation()
        except SystemExit as error:
            finish(_exit_code(error.code))
            raise
        except KeyboardInterrupt:
            finish(130)
            raise
        except BaseException:
            finish(1)
            raise
        finish(_exit_code(result))
        return result
    finally:
        _CAPTURE.reset(token)


@contextmanager
def _directory(path: Path | str, *, dir_fd: int | None = None):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=dir_fd)
    try:
        yield descriptor
    finally:
        os.close(descriptor)


def _read_file(directory_fd: int, name: str = "run.json") -> bytes:
    descriptor = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=directory_fd)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_size > _MAX_BYTES:
            raise ValueError("Invalid receipt file")
        return os.read(descriptor, _MAX_BYTES + 1)
    finally:
        os.close(descriptor)


def _validated(record: object, run_id: str, month: str, now: datetime) -> dict[str, Any] | None:
    if not isinstance(record, dict) or set(record) != _FIELDS or record.get("schema") != _SCHEMA or type(record.get("version")) is not int or record["version"] != 1:
        raise ValueError("Invalid run status")
    if record.get("run_id") != run_id or not isinstance(record.get("batch_id"), str) or record["batch_id"] not in BATCH_ARGUMENTS:
        raise ValueError("Invalid run identity")
    if not isinstance(record["invocation_id"], str) or (record["invocation_id"] and _HEX.fullmatch(record["invocation_id"]) is None):
        raise ValueError("Invalid invocation")
    if type(record["pid"]) is not int or not 0 < record["pid"] < 2**31:
        raise ValueError("Invalid process")
    if record["boot_id"] is not None and (not isinstance(record["boot_id"], str) or _BOOT.fullmatch(record["boot_id"]) is None):
        raise ValueError("Invalid boot identity")
    ticks = record["process_start_ticks"]
    if ticks is not None and (not isinstance(ticks, str) or not ticks.isdecimal() or len(ticks) > 24):
        raise ValueError("Invalid process identity")
    started = _parse_timestamp(record["started_at"])
    if started.strftime("%Y-%m") != month:
        raise ValueError("Receipt month mismatch")
    outcome, code = record["outcome"], record["exit_code"]
    if outcome == "running":
        if code is not None or record["finished_at"] is not None:
            raise ValueError("Invalid running status")
    elif outcome in ("succeeded", "failed"):
        finished = _parse_timestamp(record["finished_at"])
        if finished < started or type(code) is not int or not 0 <= code <= 255 or (outcome == "succeeded") != (code == 0):
            raise ValueError("Invalid completed status")
        if finished > now:
            return None
    else:
        raise ValueError("Invalid outcome")
    if started > now:
        return None
    result = dict(record)
    if outcome == "running":
        identity = _process_identity(record["pid"])
        if identity is None or identity != (record["boot_id"], record["process_start_ticks"]):
            result["outcome"] = "unconfirmed"
    return result


def read_fetch_run_history(root: Path, *, start: datetime, end: datetime, observed_at: datetime) -> dict[str, Any]:
    """Read bounded receipt directories only; never create files, locks or stores.

    Month directories use UTC start times. Eastern month requests may cross into
    one adjacent UTC month. The caller supplies the fixed production root.
    """
    first, last, now = (_parse_timestamp(_stamp(value)) for value in (start, end, observed_at))
    if last <= first or last - first > timedelta(days=32):
        raise ValidationError("Run history window is invalid")
    result: dict[str, Any] = {"records": [], "available": False, "truncated": False, "invalid": 0}
    try:
        with _directory(root) as root_fd:
            result["available"] = True
            cursor = first.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            months = []
            while cursor < last:
                months.append(cursor.strftime("%Y-%m"))
                cursor = (cursor + timedelta(days=32)).replace(day=1)
            budget, examined = _MAX_TOTAL_BYTES, 0
            deadline = time.monotonic() + 3
            for month in months:
                try:
                    with _directory(month, dir_fd=root_fd) as month_fd, os.scandir(month_fd) as entries:
                        for entry in entries:
                            examined += 1
                            if examined > _MAX_RUNS or budget < _MAX_BYTES + 1 or time.monotonic() > deadline:
                                result["truncated"] = True
                                return result
                            if _HEX.fullmatch(entry.name) is None:
                                result["invalid"] += 1
                                continue
                            try:
                                with _directory(entry.name, dir_fd=month_fd) as run_fd:
                                    payload = _read_file(run_fd)
                                    budget -= len(payload)
                                    value = loads_strict(payload, max_bytes=_MAX_BYTES)
                                    record = _validated(value, entry.name, month, now)
                                    if record is not None and first <= _parse_timestamp(record["started_at"]) < last:
                                        try:
                                            if budget < _MAX_BYTES + 1:
                                                result["truncated"] = True
                                                result["records"].append(record)
                                                return result
                                            summary_payload = _read_file(run_fd, "summary.json")
                                            budget -= len(summary_payload)
                                            summary = loads_strict(summary_payload, max_bytes=_MAX_BYTES)
                                            counts = _validated_summary(summary, record, now)
                                            if counts is not None:
                                                record["counts"] = counts
                                        except FileNotFoundError:
                                            pass
                                        except (OSError, ValueError, TypeError, OverflowError, QuantDataError):
                                            result["invalid"] += 1
                                        result["records"].append(record)
                            except (OSError, ValueError, TypeError, OverflowError, QuantDataError):
                                result["invalid"] += 1
                except FileNotFoundError:
                    continue
                except OSError:
                    result["invalid"] += 1
    except OSError:
        result["available"] = False
    return result
