"""Cooperative publication lock for the canonical registry/catalog bundle."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import math
import os
from pathlib import Path
import time
from typing import Final

from .errors import RegistryError


REGISTRY_BUNDLE_LOCK_TIMEOUT_SECONDS: Final = 30.0
_LOCK_DIRECTORY: Final = Path("data/.quant_data_locks")
_POLL_SECONDS: Final = 0.02


def _validated_timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Registry bundle lock timeout must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < 0 or result > 300:
        raise ValueError("Registry bundle lock timeout is out of range")
    return result


@contextmanager
def registry_bundle_lock(
    project_root: Path,
    *,
    exclusive: bool,
    timeout_seconds: float = REGISTRY_BUNDLE_LOCK_TIMEOUT_SECONDS,
) -> Iterator[None]:
    """Hold the project-local registry bundle lock in shared or exclusive mode."""

    if not isinstance(exclusive, bool):
        raise ValueError("Registry bundle lock mode must be explicit")
    timeout = _validated_timeout(timeout_seconds)
    try:
        root = Path(project_root).resolve(strict=True)
    except OSError as exc:
        raise RegistryError("Registry bundle root is unavailable") from exc
    if not root.is_dir():
        raise RegistryError("Registry bundle root is unavailable")

    try:
        import fcntl
    except ImportError as exc:  # pragma: no cover - WSL/Linux is canonical
        raise RuntimeError("Registry bundle locking requires the Linux runtime") from exc

    descriptor: int | None = None
    locked = False
    try:
        lock_directory = root / _LOCK_DIRECTORY
        lock_directory.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(lock_directory, os.O_RDONLY | os.O_DIRECTORY)
        mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(descriptor, mode | fcntl.LOCK_NB)
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RegistryError(
                        "Timed out waiting for registry bundle publication"
                    )
                time.sleep(min(_POLL_SECONDS, remaining))
            else:
                locked = True
                break
    except RegistryError:
        if descriptor is not None:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise RegistryError("Registry bundle lock is unavailable") from exc

    assert descriptor is not None
    try:
        yield
    finally:
        try:
            if locked:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
