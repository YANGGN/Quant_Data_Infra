"""Explicit four-store routing and Linux physical-path coordination."""

from __future__ import annotations

import hashlib
import math
import os
import sqlite3
import time
import weakref
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Iterable, Iterator, Mapping
from urllib.parse import quote

from .errors import ConflictError, StoreUnavailableError, ValidationError

if TYPE_CHECKING:
    from .registry import Registry


class StoreRole(StrEnum):
    MARKET = "market"
    MACRO = "macro"
    COMPANY = "company"
    NEWS = "news"


STORE_ROLES = tuple(StoreRole)


def _validated_lock_timeout(timeout_seconds: float) -> float:
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(float(timeout_seconds))
        or timeout_seconds < 0
    ):
        raise ValidationError("Lock timeout must be a finite nonnegative number")
    return float(timeout_seconds)


def _coerce_path(value: str | os.PathLike[str], role: StoreRole) -> Path:
    if value is None or not str(value).strip():
        raise ValidationError(f"Explicit {role.value} store path is required")
    supplied = Path(value).expanduser()
    if not supplied.is_absolute():
        raise ValidationError(f"Explicit {role.value} store path must be absolute")
    resolved = supplied.resolve(strict=False)
    if resolved == Path(resolved.anchor) or resolved == Path.home().resolve(strict=False):
        raise ValidationError(f"Explicit {role.value} store path is too broad")
    if resolved.exists() and not resolved.is_file():
        raise ValidationError(f"Explicit {role.value} store path must name a file")
    return resolved


@dataclass(frozen=True, slots=True)
class StoreMap:
    market: Path
    macro: Path
    company: Path
    news: Path

    def __post_init__(self) -> None:
        for role in STORE_ROLES:
            object.__setattr__(self, role.value, _coerce_path(self.path(role), role))
        self.validate_distinct()

    @classmethod
    def four_explicit(
        cls,
        *,
        market: str | os.PathLike[str],
        macro: str | os.PathLike[str],
        company: str | os.PathLike[str],
        news: str | os.PathLike[str],
    ) -> "StoreMap":
        return cls(
            market=_coerce_path(market, StoreRole.MARKET),
            macro=_coerce_path(macro, StoreRole.MACRO),
            company=_coerce_path(company, StoreRole.COMPANY),
            news=_coerce_path(news, StoreRole.NEWS),
        )

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

    def identities(self) -> tuple["PhysicalStoreIdentity", ...]:
        return tuple(
            PhysicalStoreIdentity(
                role=role,
                path=path,
                canonical_uri=canonical_path_uri(path),
                lock_key=physical_lock_key(path),
            )
            for role, path in self.items()
        )


@dataclass(frozen=True, slots=True)
class PhysicalStoreIdentity:
    role: StoreRole
    path: Path
    canonical_uri: str
    lock_key: str


def resolve_store_map(
    registry: "Registry",
    *,
    project_root: str | os.PathLike[str],
    environment: Mapping[str, str],
    explicit_paths: Mapping[str, str | os.PathLike[str]] | None = None,
) -> StoreMap:
    """Resolve exactly four host-owned paths without ambient state or CWD use."""

    root = Path(project_root).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValidationError("Project root must be an existing directory")
    env = dict(environment)
    if "QUANT_DB_PATH" in env:
        raise ValidationError("Legacy unified-store routing is retired")
    if explicit_paths is not None:
        result = StoreMap.from_mapping(explicit_paths)
    else:
        resolved: dict[str, Path] = {}
        for role in STORE_ROLES:
            declaration = registry.store(role.value)
            if declaration.path_env in env:
                raw = env[declaration.path_env]
                if not isinstance(raw, str) or not raw.strip():
                    raise ValidationError(
                        f"{declaration.path_env} cannot be an empty override"
                    )
                candidate = Path(raw).expanduser()
                if not candidate.is_absolute():
                    raise ValidationError("Store environment overrides must be absolute")
            else:
                candidate = root / declaration.default_path
            resolved[role.value] = candidate.resolve(strict=False)
        result = StoreMap.from_mapping(resolved)
    for _, path in result.items():
        if path == root or path == Path.home().resolve(strict=False):
            raise ValidationError("Store paths cannot target a broad project or home root")
    return result


def canonical_path_uri(path: Path) -> str:
    return path.resolve(strict=False).as_uri()


def physical_lock_key(path: Path) -> str:
    material = b"quant-data-sqlite-lock-v1\x00" + canonical_path_uri(path).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


class StoreWriteLock:
    """Advisory Linux lock keyed by canonical physical store identity."""

    def __init__(self, path: Path, *, timeout_seconds: float = 5.0) -> None:
        self.path = path.resolve(strict=False)
        self.timeout_seconds = _validated_lock_timeout(timeout_seconds)
        self._handle: BinaryIO | None = None

    def __enter__(self) -> "StoreWriteLock":
        return self._acquire_until(time.monotonic() + self.timeout_seconds)

    def _acquire_until(self, deadline: float) -> "StoreWriteLock":
        handle: BinaryIO | None = None
        try:
            import fcntl
        except ImportError as exc:  # pragma: no cover - WSL/Linux is canonical
            raise RuntimeError("Physical store locking requires the Linux runtime") from exc
        try:
            lock_dir = self.path.parent / ".quant_data_locks"
            lock_dir.mkdir(parents=True, exist_ok=True)
            lock_path = lock_dir / f"sqlite-{physical_lock_key(self.path)}.lock"
            handle = open(lock_path, "a+b")
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self._handle = handle
                    handle = None
                    return self
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ConflictError("Timed out acquiring the physical store lock")
                    time.sleep(0.02)
        except BaseException:
            if handle is not None:
                handle.close()
            raise

    def __exit__(self, exc_type, exc, traceback) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


@contextmanager
def acquire_write_locks(
    store_map: StoreMap,
    roles: Iterable[StoreRole | str],
    *,
    timeout_seconds: float = 5.0,
) -> Iterator[tuple[PhysicalStoreIdentity, ...]]:
    """Acquire a complete physical lock set in canonical URI order.

    All identities are resolved before the first lock file is created. One
    monotonic deadline applies to the complete set, and partial acquisition is
    always released in reverse order.
    """

    with acquire_write_session(
        store_map,
        roles,
        timeout_seconds=timeout_seconds,
    ) as session:
        yield session._legacy_identities()


def _configure_connection(connection: sqlite3.Connection, *, writer: bool) -> None:
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA recursive_triggers=ON")
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
        # One explicit read transaction gives every multi-statement repository
        # call a stable SQLite snapshot.  Query-only remains enabled, and the
        # transaction is always rolled back on context exit.
        connection.execute("BEGIN")
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
        if connection.in_transaction:
            connection.rollback()
        connection.close()


@contextmanager
def dataset_read_connection(
    store_map: StoreMap,
    registry: "Registry",
    dataset_id: str,
) -> Iterator[sqlite3.Connection]:
    """Open a host-routed read connection from registry dataset ownership."""

    matches = [dataset for dataset in registry.datasets if dataset.id == dataset_id]
    if len(matches) != 1 or not matches[0].active:
        raise ValidationError("Unknown or inactive dataset")
    with read_connection(store_map, matches[0].store) as connection:
        yield connection


def stable_id(prefix: str, *parts: str) -> str:
    material = "\x00".join(parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(material).hexdigest()[:32]}"


@dataclass(frozen=True, slots=True)
class HeldWriteLockIdentity:
    """Sanitized identity exposed by an active write-lock capability.

    Physical paths and canonical URIs deliberately remain internal to the
    session implementation. The path-derived digest is sufficient for a
    receipt or caller to correlate acquisition order without leaking a local
    filesystem layout.
    """

    role: StoreRole
    lock_key: str


@dataclass(slots=True)
class _HeldWriteLocksState:
    """Module-private ownership proof retained for one active session."""

    active: bool
    physical_identities: tuple[PhysicalStoreIdentity, ...]
    public_identities: tuple[HeldWriteLockIdentity, ...]
    acquisition_wait_seconds: tuple[float, ...]


class HeldWriteLocks:
    """Opaque capability proving a complete physical write-lock set is held.

    Instances are created exclusively by acquire_write_session.
    Their backing state is kept in a module-private weak mapping so a
    hand-constructed object has no ownership proof and is rejected by a
    coordinator before it can suppress a nested lock.
    """

    __slots__ = ("__weakref__",)

    def __init__(self) -> None:
        raise TypeError("HeldWriteLocks instances are provided by acquire_write_session")

    def __init_subclass__(cls, **kwargs: object) -> None:
        del kwargs
        raise TypeError("HeldWriteLocks cannot be subclassed")

    @property
    def active(self) -> bool:
        state = _held_write_lock_state(self)
        return state is not None and state.active

    @property
    def identities(self) -> tuple[HeldWriteLockIdentity, ...]:
        """Immutable sanitized identities in physical acquisition order."""

        state = _held_write_lock_state(self)
        return () if state is None else state.public_identities

    @property
    def acquisition_wait_seconds(self) -> tuple[float, ...]:
        """Immutable per-identity waits aligned with identities."""

        state = _held_write_lock_state(self)
        return () if state is None else state.acquisition_wait_seconds

    @property
    def roles(self) -> tuple[StoreRole, ...]:
        """Sanitized roles in the deterministic physical acquisition order."""

        return tuple(identity.role for identity in self.identities)

    def holds(self, role: StoreRole | str) -> bool:
        """Return whether this still-active session includes role."""

        try:
            normalized = StoreRole(role)
        except (TypeError, ValueError):
            return False
        state = _held_write_lock_state(self)
        return bool(
            state is not None
            and state.active
            and any(identity.role is normalized for identity in state.public_identities)
        )

    def _require_target(self, store_map: StoreMap, role: StoreRole | str) -> None:
        """Fail closed unless this session actively owns the target store."""

        if not isinstance(store_map, StoreMap):
            raise ValidationError("Held write-lock capability requires an explicit store map")
        normalized = StoreRole(role)
        state = _held_write_lock_state(self)
        if state is None or not state.active:
            raise ConflictError("Held write-lock capability is not active")
        store_map.validate_distinct()
        target_uri = canonical_path_uri(store_map.path(normalized))
        target_key = physical_lock_key(store_map.path(normalized))
        for identity in state.physical_identities:
            if identity.role is not normalized:
                continue
            if identity.canonical_uri == target_uri and identity.lock_key == target_key:
                return
            break
        raise ConflictError("Held write-lock capability does not cover the target store")

    def _legacy_identities(self) -> tuple[PhysicalStoreIdentity, ...]:
        """Return legacy identities only while the backing session is active."""

        state = _held_write_lock_state(self)
        if state is None or not state.active:
            raise ConflictError("Held write-lock capability is not active")
        return state.physical_identities


_HELD_WRITE_LOCK_STATES: weakref.WeakKeyDictionary[
    HeldWriteLocks, _HeldWriteLocksState
] = weakref.WeakKeyDictionary()


def _held_write_lock_state(capability: HeldWriteLocks) -> _HeldWriteLocksState | None:
    return _HELD_WRITE_LOCK_STATES.get(capability)


def _new_held_write_locks(state: _HeldWriteLocksState) -> HeldWriteLocks:
    """Create the sole public capability shape without invoking its constructor."""

    capability = object.__new__(HeldWriteLocks)
    _HELD_WRITE_LOCK_STATES[capability] = state
    return capability


def _ordered_write_lock_identities(
    store_map: StoreMap,
    roles: Iterable[StoreRole | str],
) -> tuple[PhysicalStoreIdentity, ...]:
    normalized_roles = tuple(dict.fromkeys(StoreRole(role) for role in roles))
    if not normalized_roles:
        raise ValidationError("At least one store lock is required")
    store_map.validate_distinct()
    identities_by_role = {identity.role: identity for identity in store_map.identities()}
    identities = tuple(
        sorted(
            (identities_by_role[role] for role in normalized_roles),
            key=lambda identity: identity.canonical_uri,
        )
    )
    if len({identity.canonical_uri for identity in identities}) != len(identities):
        raise ConflictError("Store lock identities must be physically distinct")
    return identities


def _measured_lock_wait_seconds(started_at: float, completed_at: float) -> float:
    elapsed = float(completed_at - started_at)
    if not math.isfinite(elapsed):
        raise RuntimeError("Monotonic lock clock produced an invalid duration")
    return max(0.0, elapsed)


@contextmanager
def acquire_write_session(
    store_map: StoreMap,
    roles: Iterable[StoreRole | str],
    *,
    timeout_seconds: float = 5.0,
) -> Iterator[HeldWriteLocks]:
    """Hold a complete physical write-lock set behind an opaque capability.

    All target identities are resolved before the first lock is created. A
    single monotonic deadline bounds every acquisition. The capability becomes
    invalid before the underlying locks are released in reverse order.
    """

    timeout_seconds = _validated_lock_timeout(timeout_seconds)
    identities = _ordered_write_lock_identities(store_map, roles)
    acquisition_started_at = time.monotonic()
    deadline = acquisition_started_at + timeout_seconds
    acquired: list[StoreWriteLock] = []
    acquisition_wait_seconds: list[float] = []
    state: _HeldWriteLocksState | None = None
    try:
        for identity in identities:
            wait_started_at = time.monotonic()
            lock = StoreWriteLock(identity.path, timeout_seconds=timeout_seconds)
            lock._acquire_until(deadline)
            acquired.append(lock)
            acquisition_wait_seconds.append(
                _measured_lock_wait_seconds(wait_started_at, time.monotonic())
            )
        state = _HeldWriteLocksState(
            active=True,
            physical_identities=identities,
            public_identities=tuple(
                HeldWriteLockIdentity(role=identity.role, lock_key=identity.lock_key)
                for identity in identities
            ),
            acquisition_wait_seconds=tuple(acquisition_wait_seconds),
        )
        yield _new_held_write_locks(state)
    finally:
        if state is not None:
            state.active = False
        for lock in reversed(acquired):
            lock.__exit__(None, None, None)
