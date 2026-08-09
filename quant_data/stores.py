"""Explicit four-store routing and Linux physical-path coordination."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Iterator, Mapping
from urllib.parse import quote

from .errors import ConflictError, StoreUnavailableError, ValidationError


class StoreRole(StrEnum):
    MARKET = "market"
    MACRO = "macro"
    COMPANY = "company"
    NEWS = "news"


STORE_ROLES = tuple(StoreRole)


def _coerce_path(value: str | os.PathLike[str], role: StoreRole) -> Path:
    if value is None or not str(value).strip():
        raise ValidationError(f"Explicit {role.value} store path is required")
    return Path(value).expanduser().resolve(strict=False)


@dataclass(frozen=True, slots=True)
class StoreMap:
    market: Path
    macro: Path
    company: Path
    news: Path

    @classmethod
    def four_explicit(
        cls,
        *,
        market: str | os.PathLike[str],
        macro: str | os.PathLike[str],
        company: str | os.PathLike[str],
        news: str | os.PathLike[str],
    ) -> "StoreMap":
        result = cls(
            market=_coerce_path(market, StoreRole.MARKET),
            macro=_coerce_path(macro, StoreRole.MACRO),
            company=_coerce_path(company, StoreRole.COMPANY),
            news=_coerce_path(news, StoreRole.NEWS),
        )
        result.validate_distinct()
        return result

    @classmethod
    def from_mapping(cls, paths: Mapping[str, str | os.PathLike[str]]) -> "StoreMap":
        unknown = set(paths) - {role.value for role in STORE_ROLES}
        missing = {role.value for role in STORE_ROLES} - set(paths)
        if unknown or missing:
            raise ValidationError(
                "Store map must contain exactly market, macro, company, and news"
            )
        return cls.four_explicit(
            market=paths["market"],
            macro=paths["macro"],
            company=paths["company"],
            news=paths["news"],
        )

    def path(self, role: StoreRole | str) -> Path:
        normalized = StoreRole(role)
        return getattr(self, normalized.value)

    def items(self) -> tuple[tuple[StoreRole, Path], ...]:
        return tuple((role, self.path(role)) for role in STORE_ROLES)

    def validate_distinct(self) -> None:
        items = self.items()
        normalized: dict[str, StoreRole] = {}
        for role, path in items:
            key = os.path.normcase(os.path.normpath(str(path)))
            if key in normalized:
                raise ConflictError("Operational store paths must be physically distinct")
            normalized[key] = role
        for index, (_, left) in enumerate(items):
            if not left.exists():
                continue
            for _, right in items[index + 1 :]:
                if right.exists() and os.path.samefile(left, right):
                    raise ConflictError("Operational store paths must be physically distinct")


def canonical_path_uri(path: Path) -> str:
    return path.resolve(strict=False).as_uri()


def physical_lock_key(path: Path) -> str:
    material = b"quant-data-sqlite-lock-v1\x00" + canonical_path_uri(path).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


class StoreWriteLock:
    """Advisory Linux lock keyed by canonical physical store identity."""

    def __init__(self, path: Path, *, timeout_seconds: float = 5.0) -> None:
        self.path = path.resolve(strict=False)
        self.timeout_seconds = timeout_seconds
        self._handle = None

    def __enter__(self) -> "StoreWriteLock":
        try:
            import fcntl
        except ImportError as exc:  # pragma: no cover - WSL/Linux is canonical
            raise RuntimeError("Physical store locking requires the Linux runtime") from exc
        lock_dir = self.path.parent / ".quant_data_locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / f"sqlite-{physical_lock_key(self.path)}.lock"
        handle = open(lock_path, "a+b")
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._handle = handle
                return self
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    handle.close()
                    raise ConflictError("Timed out acquiring the physical store lock")
                time.sleep(0.02)

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._handle is None:
            return
        import fcntl

        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()
        self._handle = None


def _configure_connection(connection: sqlite3.Connection, *, writer: bool) -> None:
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    if writer:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
    else:
        connection.execute("PRAGMA query_only=ON")


@contextmanager
def writer_connection(
    store_map: StoreMap,
    role: StoreRole | str,
    *,
    create_parent: bool = False,
) -> Iterator[sqlite3.Connection]:
    normalized = StoreRole(role)
    path = store_map.path(normalized)
    store_map.validate_distinct()
    if create_parent:
        path.parent.mkdir(parents=True, exist_ok=True)
    elif not path.exists():
        raise StoreUnavailableError(f"{normalized.value} store is unavailable")
    connection = sqlite3.connect(path, timeout=5.0, isolation_level=None)
    try:
        _configure_connection(connection, writer=True)
        yield connection
    finally:
        connection.close()


def _readonly_uri(path: Path) -> str:
    # SQLite URI syntax requires the slash characters to remain visible.
    return "file:" + quote(path.as_posix(), safe="/") + "?mode=ro"


@contextmanager
def read_connection(
    store_map: StoreMap,
    role: StoreRole | str,
    *,
    expected_anchor: str = "store_metadata",
) -> Iterator[sqlite3.Connection]:
    normalized = StoreRole(role)
    path = store_map.path(normalized)
    store_map.validate_distinct()
    if not path.is_file():
        raise StoreUnavailableError(f"{normalized.value} store is unavailable")
    try:
        connection = sqlite3.connect(
            _readonly_uri(path),
            uri=True,
            timeout=5.0,
            isolation_level=None,
        )
    except sqlite3.Error as exc:
        raise StoreUnavailableError(f"{normalized.value} store is unavailable") from exc
    try:
        _configure_connection(connection, writer=False)
        anchor = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (expected_anchor,),
        ).fetchone()
        if anchor is None:
            raise StoreUnavailableError(f"{normalized.value} store has the wrong role")
        role_row = connection.execute(
            "SELECT store_role FROM store_metadata WHERE singleton=1"
        ).fetchone()
        if role_row is None or role_row["store_role"] != normalized.value:
            raise StoreUnavailableError(f"{normalized.value} store has the wrong role")
        yield connection
    except sqlite3.Error as exc:
        raise StoreUnavailableError(f"{normalized.value} store is unavailable") from exc
    finally:
        connection.close()


def stable_id(prefix: str, *parts: str) -> str:
    material = "\x00".join(parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(material).hexdigest()[:32]}"
