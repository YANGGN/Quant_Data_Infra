"""Private, bounded latest-status receipts for fixed refresh sources."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from typing import Final, Iterator

from ..errors import ValidationError


_SCHEMA: Final = "quant_data.refresh_status"
_VERSION: Final = 1
_MAX_BYTES: Final = 16 * 1024
_DIR_MODE: Final = 0o700
_FILE_MODE: Final = 0o600
_SOURCE_RE: Final = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_NOTE_RE: Final = re.compile(r"[a-z0-9][a-z0-9_.-]{0,127}\Z")
_OUTCOMES: Final = frozenset({"published", "unchanged", "succeeded", "failed"})
_LOCK_NAME: Final = ".refresh-status.lock"
_LOCK_TIMEOUT_SECONDS: Final = 5.0
_LOCK_POLL_SECONDS: Final = 0.05


class RefreshStatusError(RuntimeError):
    """Sanitized private-status persistence failure."""


def _validate_source(source: object) -> str:
    if not isinstance(source, str) or _SOURCE_RE.fullmatch(source) is None:
        raise ValidationError("Refresh status source is invalid")
    return source


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValidationError("Refresh status timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError("Refresh status timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValidationError("Refresh status timestamp is invalid")
    return parsed.astimezone(timezone.utc)


def _validate_note(note: object) -> str:
    if note == "":
        return ""
    if not isinstance(note, str) or _NOTE_RE.fullmatch(note) is None:
        raise ValidationError("Refresh status note is invalid")
    if any(marker in note.lower() for marker in ("credential", "password", "secret", "token")):
        raise ValidationError("Refresh status note is invalid")
    return note


def _validated_receipt(value: object, source: str) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    if value.get("schema") != _SCHEMA or value.get("version") != _VERSION:
        return None
    if value.get("source") != source:
        return None
    if set(value) != {
        "schema", "version", "source", "last_attempt", "last_successful_fetch_at"
    }:
        return None
    attempt = value.get("last_attempt")
    if not isinstance(attempt, dict) or set(attempt) != {
        "started_at", "completed_at", "outcome", "note"
    }:
        return None
    try:
        started = _parse_timestamp(attempt["started_at"])
        completed = _parse_timestamp(attempt["completed_at"])
        if completed < started or attempt["outcome"] not in _OUTCOMES:
            return None
        _validate_note(attempt["note"])
        successful = value["last_successful_fetch_at"]
        if successful is None and attempt["outcome"] != "failed":
            return None
        if successful is not None and _parse_timestamp(successful) > completed:
            return None
    except (TypeError, ValidationError):
        return None
    return value


def _status_path(root: Path, source: str) -> Path:
    return root / f"{source}.json"


def _read_status_file(path: Path, source: str) -> dict[str, object] | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RefreshStatusError("Refresh status is unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise RefreshStatusError("Refresh status is unavailable")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_size > _MAX_BYTES:
                return None
            payload = os.read(descriptor, _MAX_BYTES + 1)
        finally:
            os.close(descriptor)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RefreshStatusError("Refresh status is unavailable") from exc
    if len(payload) > _MAX_BYTES:
        return None
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return _validated_receipt(decoded, source)


def _validate_read_root(root: object) -> Path | None:
    if not isinstance(root, Path):
        return None
    try:
        info = root.lstat()
    except (FileNotFoundError, OSError):
        return None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        return None
    return root


def read_refresh_status(root: Path, *, source: str) -> dict[str, object]:
    """Return a validated status without creating state or locks."""

    try:
        checked_source = _validate_source(source)
    except ValidationError:
        return {}
    checked_root = _validate_read_root(root)
    if checked_root is None:
        return {}
    try:
        receipt = _read_status_file(_status_path(checked_root, checked_source), checked_source)
    except RefreshStatusError:
        return {}
    if receipt is None:
        return {}
    return json.loads(json.dumps(receipt, separators=(",", ":"), sort_keys=True))


def _ensure_write_root(root: object) -> Path:
    if not isinstance(root, Path):
        raise ValidationError("Refresh status root is invalid")
    try:
        if root.exists() and root.is_symlink():
            raise RefreshStatusError("Refresh status root is unavailable")
        root.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
        info = root.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise RefreshStatusError("Refresh status root is unavailable")
        os.chmod(root, _DIR_MODE)
    except RefreshStatusError:
        raise
    except OSError as exc:
        raise RefreshStatusError("Refresh status root is unavailable") from exc
    return root


def _fsync_directory(directory: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def _write_lock(root: Path) -> Iterator[None]:
    path = root / _LOCK_NAME
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags, _FILE_MODE)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise RefreshStatusError("Refresh status lock is unavailable")
        os.chmod(path, _FILE_MODE)
        deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RefreshStatusError("Refresh status lock is unavailable")
                time.sleep(min(_LOCK_POLL_SECONDS, remaining))
            else:
                break
    except RefreshStatusError:
        if descriptor is not None:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise RefreshStatusError("Refresh status lock is unavailable") from exc
    try:
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _attempt_key(attempt: dict[str, object]) -> tuple[datetime, datetime]:
    return (_parse_timestamp(attempt["completed_at"]), _parse_timestamp(attempt["started_at"]))


def _new_receipt(
    *, source: str, started_at: str, completed_at: str, outcome: str,
    successful_fetch_at: str | None, note: str, existing: dict[str, object] | None,
) -> dict[str, object]:
    candidate_attempt = {
        "started_at": started_at,
        "completed_at": completed_at,
        "outcome": outcome,
        "note": note,
    }
    selected_attempt = candidate_attempt
    if existing is not None:
        previous_attempt = existing["last_attempt"]
        assert isinstance(previous_attempt, dict)
        if _attempt_key(candidate_attempt) < _attempt_key(previous_attempt):
            selected_attempt = previous_attempt
    prior_success = None if existing is None else existing["last_successful_fetch_at"]
    selected_success = prior_success
    if successful_fetch_at is not None:
        if selected_success is None or _parse_timestamp(successful_fetch_at) > _parse_timestamp(selected_success):
            selected_success = successful_fetch_at
    return {
        "schema": _SCHEMA,
        "version": _VERSION,
        "source": source,
        "last_attempt": selected_attempt,
        "last_successful_fetch_at": selected_success,
    }


def _replace_receipt(path: Path, payload: bytes) -> None:
    temporary: str | None = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=path.parent)
        os.chmod(temporary, _FILE_MODE)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists() and path.is_symlink():
            raise RefreshStatusError("Refresh status is unavailable")
        os.replace(temporary, path)
        temporary = None
        os.chmod(path, _FILE_MODE)
        _fsync_directory(path.parent)
    except RefreshStatusError:
        raise
    except OSError as exc:
        raise RefreshStatusError("Refresh status publication failed") from exc
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def record_refresh_attempt(
    root: Path, *, source: str, started_at: str, completed_at: str, outcome: str,
    successful_fetch_at: str | None, note: str = "",
) -> None:
    """Atomically retain the latest completed attempt for one safe source id."""

    checked_source = _validate_source(source)
    started = _parse_timestamp(started_at)
    completed = _parse_timestamp(completed_at)
    if completed < started:
        raise ValidationError("Refresh status attempt interval is invalid")
    if outcome not in _OUTCOMES:
        raise ValidationError("Refresh status outcome is invalid")
    checked_note = _validate_note(note)
    if outcome in {"published", "unchanged", "succeeded"} and successful_fetch_at is None:
        raise ValidationError("Refresh status successful fetch is invalid")
    if successful_fetch_at is not None:
        successful = _parse_timestamp(successful_fetch_at)
        if successful < started or successful > completed:
            raise ValidationError("Refresh status successful fetch is invalid")
    checked_root = _ensure_write_root(root)
    try:
        with _write_lock(checked_root):
            path = _status_path(checked_root, checked_source)
            existing = _read_status_file(path, checked_source)
            receipt = _new_receipt(
                source=checked_source, started_at=started_at, completed_at=completed_at,
                outcome=outcome, successful_fetch_at=successful_fetch_at,
                note=checked_note, existing=existing,
            )
            payload = json.dumps(
                receipt, separators=(",", ":"), sort_keys=True, ensure_ascii=True
            ).encode("utf-8")
            if len(payload) > _MAX_BYTES:
                raise RefreshStatusError("Refresh status is too large")
            _replace_receipt(path, payload)
    except (RefreshStatusError, ValidationError):
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise RefreshStatusError("Refresh status publication failed") from exc


__all__ = ("RefreshStatusError", "read_refresh_status", "record_refresh_attempt")
