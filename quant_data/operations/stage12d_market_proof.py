"""Bounded Stage 12D no-transfer market adoption/freeze proof.

The public runner is intentionally fixture-only: it accepts one strict child
of the system temporary directory, a reviewed Stage 12D scope, and synthetic
expectations plus a synthetic Stage 12C completion binding.  It cannot receive
a target path, SQL, environment, transport, or backup capability.

The zero-argument canonical entry point is private-factory bound to the one
approved project-local target.  Both paths share the same read-only proof
engine: an O_NOFOLLOW guard descriptor is opened first and SQLite can reach
the store only through that descriptor with mode=ro&immutable=1.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import fcntl
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import stat
from typing import Any, Final
import uuid

from ..errors import ConflictError, ResourceLimitError, StoreUnavailableError, ValidationError
from ..json_codec import dumps_strict, loads_strict
from ..market.stage12_scope import load_stage12_market_v1_scope
from ..market.stage12b_scope import load_stage12b_incremental_market_v1_scope
from ..market.stage12c_scope import load_stage12c_market_gap_v1_scope, stage12c_ordered_symbols
from ..market.stage12d_scope import (
    STAGE12D_RECEIPT_SCHEMA,
    STAGE12D_SCOPE_CONTRACT,
    Stage12DMarketNoTransferAdoptionScope,
    load_stage12d_market_no_transfer_adoption_scope,
    require_stage12d_market_no_transfer_adoption_scope,
)


APPROVED_PROJECT_ROOT: Final = Path("/home/volatility/Python_Projects/Quant_Data_Infra")
APPROVED_MARKET_RELATIVE_PATH: Final = Path("data/market.sqlite")
APPROVED_STAGE12C_COMPLETION_RELATIVE_PATH: Final = Path(
    "data/.stage12/market-v1/stage12c-20260813-20260814/completion.json"
)
APPROVED_STAGE12D_PRIVATE_RELATIVE_ROOT: Final = Path(
    "data/.stage12/market-v1/stage12d-no-transfer-adoption"
)
APPROVED_RETAINED_SOURCE: Final = Path(
    "/home/volatility/quant-data-nonprod/stage10-fmp-market-history-v1/stores/market.sqlite"
)

_ROOT_MODE: Final = 0o700
_SYSTEM_TEMP_ROOT: Final = Path("/tmp")
_RECEIPT_MODE: Final = 0o600
_MAX_COMPLETION_BYTES: Final = 1 * 1024 * 1024
_MAX_PROOF_RECEIPT_BYTES: Final = 128 * 1024
_COMPLETION_CONTRACT: Final = "quant_data.stage12c_market_gap_completion"
_COMPLETION_VERSION: Final = "1.0.0"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_INVOCATION_IDENTITY = re.compile(r"^[0-9a-f]{32}$")
_PROOF_RECEIPT_NAME = re.compile(r"^proof-([12])-([0-9a-f]{32})\.json$")
_UTC_MAXIMUM_LENGTH: Final = 64
_REQUIRED_TABLES: Final = frozenset(
    {
        "stage10_instruments",
        "stage10_daily_price_captures",
        "stage10_daily_price_versions",
        "stage10_daily_prices",
    }
)
_SESSION_DATES: Final = ("2026-08-13", "2026-08-14")
_AAPL: Final = "AAPL"


def _error(message: str, *, conflict: bool = False) -> ValidationError | ConflictError:
    if conflict:
        return ConflictError(message)
    return ValidationError(message)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: object) -> str:
    return _sha256_bytes(dumps_strict(value).encode("utf-8"))


def _require_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValidationError(f"Stage 12D {label} must be a lowercase SHA-256 digest")
    return value


def _require_int(value: object, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValidationError(f"Stage 12D {label} is invalid")
    return value


def _require_text(value: object, label: str, *, maximum: int = 256) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValidationError(f"Stage 12D {label} is invalid")
    return value


def _parse_proof_receipt_name(value: object) -> tuple[int, str]:
    if not isinstance(value, str):
        raise ConflictError("Stage 12D proof receipt filename is invalid")
    matched = _PROOF_RECEIPT_NAME.fullmatch(value)
    if matched is None:
        raise ConflictError("Stage 12D proof receipt filename is invalid")
    return int(matched.group(1)), matched.group(2)


def _proof_receipt_name(ordinal: int, invocation_identity: str) -> str:
    if ordinal not in {1, 2} or _INVOCATION_IDENTITY.fullmatch(invocation_identity) is None:
        raise ValidationError("Stage 12D proof receipt identity is invalid")
    return f"proof-{ordinal}-{invocation_identity}.json"


def _utc_text(value: object) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError("Stage 12D proof clock is invalid")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _require_no_follow_flag() -> int:
    value = getattr(os, "O_NOFOLLOW", None)
    if not isinstance(value, int) or value == 0:
        raise StoreUnavailableError("Stage 12D cannot enforce no-follow file access")
    return value


def _open_flags(*, write: bool = False, create: bool = False, exclusive: bool = False) -> int:
    flags = os.O_CLOEXEC | _require_no_follow_flag()
    flags |= os.O_WRONLY if write else os.O_RDONLY
    if create:
        flags |= os.O_CREAT
    if exclusive:
        flags |= os.O_EXCL
    return flags


@dataclass(frozen=True, slots=True)
class Stage12DFileStamp:
    """Stable metadata used for exact no-mutation comparisons.

    Access time is intentionally omitted: it is neither required by the
    accepted contract nor stable across mount policies.  The remaining fields
    detect replacement, link changes, size changes, data writes, and metadata
    writes.
    """

    exists: bool
    device: int | None = None
    inode: int | None = None
    mode: int | None = None
    link_count: int | None = None
    size: int | None = None
    mtime_ns: int | None = None
    ctime_ns: int | None = None

    @classmethod
    def absent(cls) -> "Stage12DFileStamp":
        return cls(exists=False)

    @classmethod
    def from_stat(cls, info: os.stat_result) -> "Stage12DFileStamp":
        return cls(
            exists=True,
            device=int(info.st_dev),
            inode=int(info.st_ino),
            mode=int(info.st_mode),
            link_count=int(info.st_nlink),
            size=int(info.st_size),
            mtime_ns=int(info.st_mtime_ns),
            ctime_ns=int(info.st_ctime_ns),
        )

    def manifest_mapping(self) -> dict[str, object]:
        if not self.exists:
            return {"exists": False}
        return {
            "ctime_ns": self.ctime_ns,
            "device": self.device,
            "exists": True,
            "inode": self.inode,
            "kind": "regular",
            "link_count": self.link_count,
            "mode": self.mode,
            "mtime_ns": self.mtime_ns,
            "size": self.size,
        }

    def identity_sha256(self) -> str:
        if not self.exists or self.device is None or self.inode is None or self.link_count is None:
            raise ValidationError("Stage 12D missing file identity is invalid")
        return _sha256_json(
            {
                "device": self.device,
                "inode": self.inode,
                "nlink": self.link_count,
            }
        )


def _same_identity(left: Stage12DFileStamp, right: Stage12DFileStamp) -> bool:
    return (
        left.exists
        and right.exists
        and left.device == right.device
        and left.inode == right.inode
        and left.mode == right.mode
        and left.link_count == right.link_count
        and left.size == right.size
        and left.mtime_ns == right.mtime_ns
        and left.ctime_ns == right.ctime_ns
    )


def _safe_lstat(path: Path, label: str, *, allow_absent: bool) -> Stage12DFileStamp:
    try:
        info = path.lstat()
    except FileNotFoundError:
        if allow_absent:
            return Stage12DFileStamp.absent()
        raise StoreUnavailableError(f"Stage 12D {label} is unavailable")
    except OSError as exc:
        raise StoreUnavailableError(f"Stage 12D {label} is unavailable") from exc
    if stat.S_ISLNK(info.st_mode):
        raise ConflictError(f"Stage 12D {label} must not be a symlink")
    if not stat.S_ISREG(info.st_mode):
        raise ConflictError(f"Stage 12D {label} must be a regular file")
    if int(info.st_uid) != os.getuid():
        raise ConflictError(f"Stage 12D {label} has an unexpected owner")
    return Stage12DFileStamp.from_stat(info)


def _regular_stamp(
    path: Path,
    label: str,
    *,
    allow_absent: bool,
    require_single_link: bool = False,
    allow_zero_only: bool = False,
) -> Stage12DFileStamp:
    stamp = _safe_lstat(path, label, allow_absent=allow_absent)
    if not stamp.exists:
        return stamp
    if require_single_link and stamp.link_count != 1:
        raise ConflictError(f"Stage 12D {label} must have one physical link")
    if allow_zero_only and stamp.size != 0:
        raise ConflictError(f"Stage 12D {label} must be absent or zero bytes")
    try:
        followed = path.stat()
    except OSError as exc:
        raise StoreUnavailableError(f"Stage 12D {label} is unavailable") from exc
    followed_stamp = Stage12DFileStamp.from_stat(followed)
    if not _same_identity(stamp, followed_stamp):
        raise ConflictError(f"Stage 12D {label} changed while being inspected")
    return stamp


def _require_direct_directory(path: Path, label: str, *, mode: int | None = None, require_owner: bool = True) -> None:
    try:
        info = path.lstat()
        followed = path.stat()
    except OSError as exc:
        raise StoreUnavailableError(f"Stage 12D {label} is unavailable") from exc
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or not stat.S_ISDIR(followed.st_mode)
        or int(info.st_dev) != int(followed.st_dev)
        or int(info.st_ino) != int(followed.st_ino)
        or (require_owner and int(info.st_uid) != os.getuid())
    ):
        raise ConflictError(f"Stage 12D {label} must be a direct owned directory")
    if mode is not None and stat.S_IMODE(info.st_mode) != mode:
        raise ConflictError(f"Stage 12D {label} has an unsafe mode")


def _require_relative_components(project: Path, relative: Path, label: str) -> Path:
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ValidationError(f"Stage 12D {label} path is invalid")
    current = project
    for part in relative.parts[:-1]:
        current = current / part
        _require_direct_directory(current, f"{label} parent")
    return project / relative


def _resolved_system_temp_root() -> Path:
    try:
        root = _SYSTEM_TEMP_ROOT.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise StoreUnavailableError("Stage 12D system temporary root is unavailable") from exc
    _require_direct_directory(root, "system temporary root", require_owner=False)
    return root


def _require_fixture_project_root(value: str | Path) -> Path:
    if isinstance(value, bool) or not isinstance(value, (str, Path)):
        raise ValidationError("Stage 12D fixture project root is required")
    supplied = Path(value)
    if not supplied.is_absolute() or supplied.is_symlink():
        raise ValidationError("Stage 12D public runner accepts only direct temporary roots")
    try:
        project = supplied.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise StoreUnavailableError("Stage 12D fixture project root is unavailable") from exc
    if supplied != project or project == APPROVED_PROJECT_ROOT or project == APPROVED_RETAINED_SOURCE:
        raise ValidationError("Stage 12D public runner accepts fixture roots only")
    _require_direct_directory(project, "fixture project root")
    temporary_root = _resolved_system_temp_root()
    try:
        relative = project.relative_to(temporary_root)
    except ValueError as exc:
        raise ValidationError("Stage 12D fixture root must be under the system temporary root") from exc
    if len(relative.parts) != 1 or ".." in relative.parts:
        raise ValidationError("Stage 12D fixture root must be under the system temporary root")
    former_default = project / "data" / "market_data.sqlite"
    if former_default.exists() or former_default.is_symlink():
        raise ValidationError("Stage 12D public runner rejects the former market default")
    return project


def _fsync_directory(path: Path) -> None:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        os.fsync(descriptor)
    except OSError as exc:
        raise StoreUnavailableError("Stage 12D private receipt directory cannot be synchronized") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _ensure_private_receipt_root(project: Path, relative: Path) -> Path:
    root = _require_relative_components(project, relative, "private receipt")
    if root.exists() or root.is_symlink():
        _require_direct_directory(root, "private receipt root", mode=_ROOT_MODE)
        return root
    parent = root.parent
    _require_direct_directory(parent, "private receipt parent")
    try:
        os.mkdir(root, _ROOT_MODE)
        os.chmod(root, _ROOT_MODE)
        _fsync_directory(parent)
    except FileExistsError:
        pass
    except OSError as exc:
        raise StoreUnavailableError("Stage 12D private receipt root cannot be created") from exc
    _require_direct_directory(root, "private receipt root", mode=_ROOT_MODE)
    return root


@dataclass(frozen=True, slots=True)
class _TargetGuard:
    path: Path
    descriptor: int
    initial_stamp: Stage12DFileStamp

    @classmethod
    def open(cls, path: Path) -> "_TargetGuard":
        pre = _regular_stamp(
            path,
            "market target",
            allow_absent=False,
            require_single_link=True,
        )
        descriptor: int | None = None
        try:
            descriptor = os.open(path, _open_flags())
            opened = Stage12DFileStamp.from_stat(os.fstat(descriptor))
            post = _regular_stamp(
                path,
                "market target",
                allow_absent=False,
                require_single_link=True,
            )
            if not _same_identity(pre, opened) or not _same_identity(pre, post):
                raise ConflictError("Stage 12D market target changed while opening its guard")
            return cls(path=path, descriptor=descriptor, initial_stamp=pre)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise ConflictError("Stage 12D market target must not be a symlink") from exc
            raise StoreUnavailableError("Stage 12D market target cannot be guarded") from exc
        except Exception:
            if descriptor is not None:
                os.close(descriptor)
            raise

    def recheck(self, label: str) -> None:
        try:
            opened = Stage12DFileStamp.from_stat(os.fstat(self.descriptor))
        except OSError as exc:
            raise StoreUnavailableError("Stage 12D market target guard is unavailable") from exc
        current = _regular_stamp(
            self.path,
            "market target",
            allow_absent=False,
            require_single_link=True,
        )
        if not _same_identity(self.initial_stamp, opened) or not _same_identity(
            self.initial_stamp, current
        ):
            raise ConflictError(f"Stage 12D market target changed {label}")

    def close(self) -> None:
        try:
            os.close(self.descriptor)
        except OSError as exc:
            raise StoreUnavailableError("Stage 12D market target guard cannot close") from exc


def _sidecar_stamps(target: Path) -> dict[str, Stage12DFileStamp]:
    return {
        "journal": _regular_stamp(
            target.with_name(target.name + "-journal"),
            "market rollback journal",
            allow_absent=True,
            allow_zero_only=True,
        ),
        "main": _regular_stamp(
            target,
            "market target",
            allow_absent=False,
            require_single_link=True,
        ),
        "shm": _regular_stamp(
            target.with_name(target.name + "-shm"),
            "market shared-memory sidecar",
            allow_absent=True,
        ),
        "wal": _regular_stamp(
            target.with_name(target.name + "-wal"),
            "market WAL sidecar",
            allow_absent=True,
            allow_zero_only=True,
        ),
    }


def _stamps_mapping(stamps: Mapping[str, Stage12DFileStamp]) -> dict[str, object]:
    return {name: stamps[name].manifest_mapping() for name in ("main", "wal", "shm", "journal")}


def _require_stamps_unchanged(
    before: Mapping[str, Stage12DFileStamp],
    target: Path,
    label: str,
) -> dict[str, Stage12DFileStamp]:
    after = _sidecar_stamps(target)
    if dict(before) != after:
        raise ConflictError(f"Stage 12D market target or sidecars changed {label}")
    return after


def _require_private_receipt_stamp(stamp: Stage12DFileStamp, label: str) -> None:
    if (
        not stamp.exists
        or stamp.link_count != 1
        or stamp.mode is None
        or stat.S_IMODE(stamp.mode) != _RECEIPT_MODE
    ):
        raise ConflictError(f"Stage 12D {label} must be one private regular receipt")


def _read_descriptor_payload(
    descriptor: int,
    stamp: Stage12DFileStamp,
    label: str,
    *,
    maximum: int,
) -> bytes:
    if stamp.size is None or stamp.size > maximum:
        raise ResourceLimitError(f"Stage 12D {label} exceeds the reviewed byte bound")
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        remaining = stamp.size
        while remaining:
            block = os.read(descriptor, min(65_536, remaining))
            if not block:
                raise ConflictError(f"Stage 12D {label} ended while being read")
            chunks.append(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise ConflictError(f"Stage 12D {label} changed while being read")
        return b"".join(chunks)
    except OSError as exc:
        raise StoreUnavailableError(f"Stage 12D {label} cannot be read") from exc


@dataclass(slots=True)
class _DirectJsonGuard:
    """Retain one direct JSON authority file and revalidate it at each boundary."""

    path: Path
    label: str
    descriptor: int
    initial_stamp: Stage12DFileStamp
    payload: bytes
    parsed: Mapping[str, object]
    maximum: int
    private_receipt: bool

    def recheck(self, boundary: str) -> None:
        try:
            opened = Stage12DFileStamp.from_stat(os.fstat(self.descriptor))
        except OSError as exc:
            raise StoreUnavailableError(f"Stage 12D {self.label} guard is unavailable") from exc
        current = _regular_stamp(
            self.path,
            self.label,
            allow_absent=False,
            require_single_link=True,
        )
        if self.private_receipt:
            _require_private_receipt_stamp(opened, self.label)
            _require_private_receipt_stamp(current, self.label)
        if not _same_identity(self.initial_stamp, opened) or not _same_identity(
            self.initial_stamp, current
        ):
            raise ConflictError(f"Stage 12D {self.label} changed {boundary}")
        payload = _read_descriptor_payload(
            self.descriptor,
            self.initial_stamp,
            self.label,
            maximum=self.maximum,
        )
        if payload != self.payload or _sha256_bytes(payload) != _sha256_bytes(self.payload):
            raise ConflictError(f"Stage 12D {self.label} content changed {boundary}")

    def close(self) -> None:
        try:
            os.close(self.descriptor)
        except OSError as exc:
            raise StoreUnavailableError(f"Stage 12D {self.label} guard cannot close") from exc


def _open_direct_json_guard(
    path: Path,
    label: str,
    *,
    maximum: int,
    private_receipt: bool = False,
) -> _DirectJsonGuard:
    pre = _regular_stamp(
        path,
        label,
        allow_absent=False,
        require_single_link=True,
    )
    if private_receipt:
        _require_private_receipt_stamp(pre, label)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, _open_flags())
        opened = Stage12DFileStamp.from_stat(os.fstat(descriptor))
        if private_receipt:
            _require_private_receipt_stamp(opened, label)
        post = _regular_stamp(
            path,
            label,
            allow_absent=False,
            require_single_link=True,
        )
        if private_receipt:
            _require_private_receipt_stamp(post, label)
        if not _same_identity(pre, opened) or not _same_identity(pre, post):
            raise ConflictError(f"Stage 12D {label} changed while opening")
        payload = _read_descriptor_payload(descriptor, opened, label, maximum=maximum)
        parsed = loads_strict(payload, max_bytes=maximum)
        if not isinstance(parsed, Mapping) or not all(isinstance(key, str) for key in parsed):
            raise ValidationError(f"Stage 12D {label} must contain an object")
        result = _DirectJsonGuard(
            path=path,
            label=label,
            descriptor=descriptor,
            initial_stamp=opened,
            payload=payload,
            parsed=parsed,
            maximum=maximum,
            private_receipt=private_receipt,
        )
        descriptor = None
        result.recheck("after opening")
        return result
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ConflictError(f"Stage 12D {label} must not be a symlink") from exc
        raise StoreUnavailableError(f"Stage 12D {label} cannot be read") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)




@dataclass(frozen=True, slots=True)
class Stage12DMarketProofReport:
    """Path-, secret-, and raw-body-free result of one successful proof."""

    proof_ordinal: int
    receipt_sha256: str
    semantic_proof_sha256: str
    scope_manifest_sha256: str
    completion_receipt_sha256: str
    target_identity_sha256: str

    def to_primitive(self) -> dict[str, object]:
        return {
            "completion_receipt_sha256": self.completion_receipt_sha256,
            "proof_ordinal": self.proof_ordinal,
            "receipt_sha256": self.receipt_sha256,
            "scope_manifest_sha256": self.scope_manifest_sha256,
            "semantic_proof_sha256": self.semantic_proof_sha256,
            "target_identity_sha256": self.target_identity_sha256,
        }


@dataclass(frozen=True, slots=True)
class Stage12DFixtureTerminalOutcome:
    """One immutable synthetic terminal Stage 12C outcome."""

    ordinal: int
    symbol: str
    outcome: str
    http_status: int

    def __post_init__(self) -> None:
        _require_int(self.ordinal, "fixture terminal ordinal", minimum=1)
        _require_text(self.symbol, "fixture terminal symbol")
        if self.outcome == "noncoverage_empty" and self.http_status == 200:
            return
        if self.outcome == "noncoverage_http_402_authorized" and self.http_status == 402:
            return
        raise ValidationError("Stage 12D fixture terminal outcome is invalid")

    def to_primitive(self) -> dict[str, object]:
        return {
            "http_status": self.http_status,
            "ordinal": self.ordinal,
            "outcome": self.outcome,
            "symbol": self.symbol,
        }


def _fixture_published_symbols(value: object) -> tuple[str, ...]:
    if not isinstance(value, tuple) or not value:
        raise ValidationError("Stage 12D fixture published symbols are invalid")
    symbols = tuple(_require_text(symbol, "fixture published symbol") for symbol in value)
    if len(set(symbols)) != len(symbols) or _AAPL not in symbols:
        raise ValidationError("Stage 12D fixture published symbols are invalid")
    return symbols


def _fixture_terminal_outcomes(value: object) -> tuple[Stage12DFixtureTerminalOutcome, ...]:
    if not isinstance(value, tuple):
        raise ValidationError("Stage 12D fixture terminal outcomes are invalid")
    outcomes: list[Stage12DFixtureTerminalOutcome] = []
    seen_ordinals: set[int] = set()
    seen_symbols: set[str] = set()
    for item in value:
        if not isinstance(item, Stage12DFixtureTerminalOutcome):
            raise ValidationError("Stage 12D fixture terminal outcome is invalid")
        if item.ordinal in seen_ordinals or item.symbol in seen_symbols:
            raise ValidationError("Stage 12D fixture terminal outcomes are duplicated")
        seen_ordinals.add(item.ordinal)
        seen_symbols.add(item.symbol)
        outcomes.append(item)
    return tuple(outcomes)


@dataclass(frozen=True, slots=True)
class Stage12DFixtureExpectations:
    """Synthetic expectations accepted only by the public temporary-root seam."""

    fixture_id: str
    current_rows: int
    version_rows: int
    captures: int
    stage12c_current_rows: int
    stage12c_version_rows: int
    stage12c_captures: int
    aapl_current_rows: int
    published_symbols: tuple[str, ...] = (_AAPL,)
    foreign_key_violations: int = 0
    duplicate_current: int = 0
    duplicate_versions: int = 0
    duplicate_captures: int = 0
    pointer_anomalies: int = 0
    terminal_database_facts: int = 0
    stage12c_availability_anomalies: int = 0
    stage12c_correction_anomalies: int = 0

    def __post_init__(self) -> None:
        fixture_id = _require_text(self.fixture_id, "fixture expectation identity")
        if not fixture_id.startswith("fixture-"):
            raise ValidationError("Stage 12D fixture expectation identity is invalid")
        symbols = _fixture_published_symbols(self.published_symbols)
        values = (
            self.current_rows,
            self.version_rows,
            self.captures,
            self.stage12c_current_rows,
            self.stage12c_version_rows,
            self.stage12c_captures,
            self.aapl_current_rows,
            self.foreign_key_violations,
            self.duplicate_current,
            self.duplicate_versions,
            self.duplicate_captures,
            self.pointer_anomalies,
            self.stage12c_availability_anomalies,
            self.stage12c_correction_anomalies,
            self.terminal_database_facts,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
            raise ValidationError("Stage 12D fixture expectations are invalid")
        if (
            self.stage12c_current_rows != self.stage12c_version_rows
            or self.stage12c_current_rows != len(symbols) * len(_SESSION_DATES)
            or self.stage12c_captures != len(symbols)
            or self.aapl_current_rows != len(_SESSION_DATES)
            or self.current_rows < self.stage12c_current_rows
            or self.version_rows < self.stage12c_version_rows
            or self.captures < self.stage12c_captures
            or self.stage12c_availability_anomalies != 0
            or self.stage12c_correction_anomalies != 0
        ):
            raise ValidationError("Stage 12D fixture expectations must be a bounded synthetic proof")


@dataclass(frozen=True, slots=True)
class Stage12DFixtureCompletion:
    """Synthetic Stage 12C receipt binding for a temporary fixture only."""

    fixture_id: str
    receipt_sha256: str
    plan_sha256: str
    scope_semantic_sha256: str
    published_complete: int
    successful_empty: int
    authorized_http_402: int
    closed: int
    terminal_outcomes: tuple[Stage12DFixtureTerminalOutcome, ...] = ()

    def __post_init__(self) -> None:
        fixture_id = _require_text(self.fixture_id, "fixture completion identity")
        if not fixture_id.startswith("fixture-"):
            raise ValidationError("Stage 12D fixture completion identity is invalid")
        for value, label in (
            (self.receipt_sha256, "fixture completion receipt digest"),
            (self.plan_sha256, "fixture completion plan digest"),
            (self.scope_semantic_sha256, "fixture completion scope digest"),
        ):
            _require_sha256(value, label)
        values = (
            self.published_complete,
            self.successful_empty,
            self.authorized_http_402,
            self.closed,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
            raise ValidationError("Stage 12D fixture completion ledger is invalid")
        terminal_outcomes = _fixture_terminal_outcomes(self.terminal_outcomes)
        empty = sum(item.outcome == "noncoverage_empty" for item in terminal_outcomes)
        authorized = sum(
            item.outcome == "noncoverage_http_402_authorized" for item in terminal_outcomes
        )
        if (
            self.successful_empty != empty
            or self.authorized_http_402 != authorized
            or self.closed != self.published_complete + empty + authorized
        ):
            raise ValidationError("Stage 12D fixture completion must be a bounded synthetic ledger")


@dataclass(frozen=True, slots=True)
class Stage12DFixtureHooks:
    """Optional temporary-fixture race hooks; never available canonically."""

    before_connect: Callable[[], None] | None = None
    before_query: Callable[[str], None] | None = None
    before_receipt: Callable[[], None] | None = None
    after_receipt: Callable[[], None] | None = None

    def __post_init__(self) -> None:
        for value in (
            self.before_connect,
            self.before_query,
            self.before_receipt,
            self.after_receipt,
        ):
            if value is not None and not callable(value):
                raise ValidationError("Stage 12D fixture hook is invalid")


@dataclass(frozen=True, slots=True)
class _ProofExpectations:
    current_rows: int
    version_rows: int
    captures: int
    stage12c_current_rows: int
    stage12c_version_rows: int
    stage12c_captures: int
    aapl_current_rows: int
    foreign_key_violations: int
    duplicate_current: int
    duplicate_versions: int
    duplicate_captures: int
    pointer_anomalies: int
    terminal_database_facts: int

    stage12c_availability_anomalies: int
    stage12c_correction_anomalies: int
    published_symbols: tuple[str, ...] | None


@dataclass(frozen=True, slots=True)
class _CompletionExpectation:
    receipt_sha256: str
    fixture_id: str | None
    plan_sha256: str
    scope_semantic_sha256: str
    published_complete: int
    successful_empty: int
    authorized_http_402: int
    closed: int
    canonical: bool
    terminal_outcomes: tuple[Stage12DFixtureTerminalOutcome, ...] | None


def _fixture_expectations(value: Stage12DFixtureExpectations) -> _ProofExpectations:
    if not isinstance(value, Stage12DFixtureExpectations):
        raise ValidationError("Stage 12D public runner requires synthetic fixture expectations")
    return _ProofExpectations(
        current_rows=value.current_rows,
        version_rows=value.version_rows,
        captures=value.captures,
        stage12c_current_rows=value.stage12c_current_rows,
        stage12c_version_rows=value.stage12c_version_rows,
        stage12c_captures=value.stage12c_captures,
        aapl_current_rows=value.aapl_current_rows,
        foreign_key_violations=value.foreign_key_violations,
        duplicate_current=value.duplicate_current,
        duplicate_versions=value.duplicate_versions,
        duplicate_captures=value.duplicate_captures,
        pointer_anomalies=value.pointer_anomalies,
        terminal_database_facts=value.terminal_database_facts,
        stage12c_availability_anomalies=value.stage12c_availability_anomalies,
        stage12c_correction_anomalies=value.stage12c_correction_anomalies,
        published_symbols=_fixture_published_symbols(value.published_symbols),
    )


def _fixture_completion(value: Stage12DFixtureCompletion) -> _CompletionExpectation:
    if not isinstance(value, Stage12DFixtureCompletion):
        raise ValidationError("Stage 12D public runner requires a synthetic completion binding")
    return _CompletionExpectation(
        receipt_sha256=value.receipt_sha256,
        fixture_id=value.fixture_id,
        plan_sha256=value.plan_sha256,
        scope_semantic_sha256=value.scope_semantic_sha256,
        published_complete=value.published_complete,
        successful_empty=value.successful_empty,
        authorized_http_402=value.authorized_http_402,
        closed=value.closed,
        canonical=False,
        terminal_outcomes=_fixture_terminal_outcomes(value.terminal_outcomes),
    )


def _canonical_expectations(scope: Stage12DMarketNoTransferAdoptionScope) -> _ProofExpectations:
    expected = scope.expected_database
    return _ProofExpectations(
        current_rows=expected.current_rows,
        version_rows=expected.version_rows,
        captures=expected.captures,
        stage12c_current_rows=expected.stage12c_current_rows,
        stage12c_version_rows=expected.stage12c_version_rows,
        stage12c_captures=expected.stage12c_captures,
        aapl_current_rows=2,
        foreign_key_violations=expected.foreign_key_violations,
        duplicate_current=expected.duplicate_current,
        duplicate_versions=0,
        duplicate_captures=0,
        pointer_anomalies=expected.invalid_current_pointer,
        terminal_database_facts=expected.terminal_database_facts,
        stage12c_availability_anomalies=0,
        stage12c_correction_anomalies=0,
        published_symbols=None,
    )


def _expected_check_results(expectations: _ProofExpectations) -> dict[str, object]:
    return {
        "aapl_current_rows": expectations.aapl_current_rows,
        "captures": expectations.captures,
        "current_rows": expectations.current_rows,
        "duplicate_captures": expectations.duplicate_captures,
        "duplicate_current": expectations.duplicate_current,
        "duplicate_versions": expectations.duplicate_versions,
        "foreign_key_violations": expectations.foreign_key_violations,
        "integrity": "ok",
        "pointer_anomalies": expectations.pointer_anomalies,
        "stage12c_availability_anomalies": expectations.stage12c_availability_anomalies,
        "stage12c_captures": expectations.stage12c_captures,
        "stage12c_correction_anomalies": expectations.stage12c_correction_anomalies,
        "stage12c_current_rows": expectations.stage12c_current_rows,
        "stage12c_version_rows": expectations.stage12c_version_rows,
        "terminal_database_facts": expectations.terminal_database_facts,
        "version_rows": expectations.version_rows,
    }

def _canonical_completion(scope: Stage12DMarketNoTransferAdoptionScope) -> _CompletionExpectation:
    stage12c = scope.stage12c
    return _CompletionExpectation(
        receipt_sha256=stage12c.completion_receipt_sha256,
        fixture_id=None,
        plan_sha256=stage12c.plan_sha256,
        scope_semantic_sha256=stage12c.scope_semantic_sha256,
        published_complete=stage12c.published_complete,
        successful_empty=stage12c.successful_empty,
        authorized_http_402=stage12c.authorized_http_402,
        closed=stage12c.closed,
        canonical=True,
        terminal_outcomes=None,
    )


def _completion_material(receipt: Mapping[str, object]) -> dict[str, object]:
    if set(receipt) != {
        "baseline_sha256",
        "contract",
        "final_target",
        "final_target_checks",
        "plan_sha256",
        "published_unit_count",
        "result_sha256s",
        "scope_manifest_sha256",
        "sha256",
        "source_final",
        "static_preflight_sha256",
        "terminal_noncoverage_count",
        "terminal_outcomes",
        "total_attempt_count",
        "version",
    }:
        raise ConflictError("Stage 12D completion receipt shape is invalid")
    return {
        key: value
        for key, value in receipt.items()
        if key not in {"contract", "sha256"}
    }


def _fixture_completion_authority_sha256(fixture_id: str) -> str:
    """Bind a fixture name into its synthetic receipt without using target identity."""

    verified = _require_text(fixture_id, "fixture completion identity")
    if not verified.startswith("fixture-"):
        raise ValidationError("Stage 12D fixture completion identity is invalid")
    return _sha256_json({"fixture_id": verified, "kind": "fixture_completion"})


def _semantic_completion_authority(completion: _CompletionExpectation) -> dict[str, object]:
    logical = {
        "authorized_http_402": completion.authorized_http_402,
        "closed": completion.closed,
        "plan_sha256": completion.plan_sha256,
        "published_complete": completion.published_complete,
        "scope_semantic_sha256": completion.scope_semantic_sha256,
        "successful_empty": completion.successful_empty,
    }
    if completion.canonical:
        if completion.fixture_id is not None:
            raise ValidationError("Stage 12D canonical completion cannot use fixture authority")
        return {
            "kind": "canonical_stage12c_completion",
            "receipt_sha256": completion.receipt_sha256,
            **logical,
        }
    if completion.fixture_id is None:
        raise ValidationError("Stage 12D fixture completion authority is missing")
    return {
        "fixture_id": _require_text(completion.fixture_id, "fixture completion identity"),
        "kind": "fixture_completion",
        **logical,
    }

def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise ConflictError(f"Stage 12D {label} is invalid")
    return value


def _exact_mapping_keys(value: Mapping[str, object], keys: frozenset[str], label: str) -> None:
    if set(value) != keys:
        raise ConflictError(f"Stage 12D {label} shape is invalid")


def _completion_file_identity(value: object, label: str) -> Mapping[str, object]:
    mapping = _mapping(value, label)
    _exact_mapping_keys(
        mapping,
        frozenset({"byte_count", "identity_sha256", "mtime_ns", "sha256"}),
        label,
    )
    _require_int(mapping["byte_count"], f"{label} byte count")
    _require_sha256(mapping["identity_sha256"], f"{label} identity digest")
    _require_int(mapping["mtime_ns"], f"{label} modification timestamp")
    _require_sha256(mapping["sha256"], f"{label} digest")
    return mapping


def _completion_checks(value: object, label: str) -> Mapping[str, object]:
    mapping = _mapping(value, label)
    _exact_mapping_keys(
        mapping,
        frozenset(
            {
                "current_price_rows",
                "daily_price_captures",
                "foreign_key_violation_count",
                "immutable_price_versions",
                "integrity_check",
                "scoped_current_row_count",
                "stage10_capture_duplicate_count",
                "stage10_current_duplicate_count",
                "stage10_current_pointer_anomaly_count",
                "stage10_version_duplicate_count",
            }
        ),
        label,
    )
    for key in (
        "current_price_rows",
        "daily_price_captures",
        "foreign_key_violation_count",
        "immutable_price_versions",
        "scoped_current_row_count",
        "stage10_capture_duplicate_count",
        "stage10_current_duplicate_count",
        "stage10_current_pointer_anomaly_count",
        "stage10_version_duplicate_count",
    ):
        _require_int(mapping[key], f"{label} {key}")
    if mapping["integrity_check"] != "ok":
        raise ConflictError("Stage 12D completion receipt integrity binding is invalid")
    return mapping


def _terminal_ledger(
    value: object,
    expectation: _CompletionExpectation,
) -> tuple[Stage12DFixtureTerminalOutcome, ...]:
    """Parse and return the receipt-sealed exact terminal identities."""

    if not isinstance(value, list):
        raise ConflictError("Stage 12D completion receipt terminal outcomes are invalid")
    outcomes: list[Stage12DFixtureTerminalOutcome] = []
    seen_ordinals: set[int] = set()
    seen_symbols: set[str] = set()
    for index, item in enumerate(value):
        mapping = _mapping(item, f"completion terminal outcome {index}")
        _exact_mapping_keys(
            mapping,
            frozenset({"http_status", "ordinal", "outcome", "symbol"}),
            "completion terminal outcome",
        )
        outcome = Stage12DFixtureTerminalOutcome(
            ordinal=_require_int(mapping["ordinal"], "completion terminal ordinal", minimum=1),
            symbol=_require_text(mapping["symbol"], "completion terminal symbol"),
            outcome=_require_text(mapping["outcome"], "completion terminal outcome"),
            http_status=_require_int(mapping["http_status"], "completion terminal status"),
        )
        if outcome.ordinal in seen_ordinals or outcome.symbol in seen_symbols:
            raise ConflictError("Stage 12D completion receipt terminal outcomes are duplicated")
        seen_ordinals.add(outcome.ordinal)
        seen_symbols.add(outcome.symbol)
        outcomes.append(outcome)
    exact = tuple(outcomes)
    empty = sum(item.outcome == "noncoverage_empty" for item in exact)
    authorized = sum(item.outcome == "noncoverage_http_402_authorized" for item in exact)
    if (
        empty != expectation.successful_empty
        or authorized != expectation.authorized_http_402
        or len(exact) != expectation.successful_empty + expectation.authorized_http_402
        or (
            expectation.terminal_outcomes is not None
            and exact != expectation.terminal_outcomes
        )
    ):
        raise ConflictError("Stage 12D completion receipt ledger binding is invalid")
    return exact


def _validate_completion_receipt(
    receipt: Mapping[str, object],
    payload: bytes,
    *,
    expectation: _CompletionExpectation,
    target: Stage12DFileStamp,
    checks: _ProofExpectations,
) -> tuple[Stage12DFixtureTerminalOutcome, ...]:
    material = _completion_material(receipt)
    receipt_sha = _require_sha256(receipt["sha256"], "completion receipt digest")
    if _sha256_json(material) != receipt_sha or receipt_sha != expectation.receipt_sha256:
        raise ConflictError("Stage 12D completion receipt digest binding is invalid")
    if not payload:
        raise ConflictError("Stage 12D completion receipt is empty")
    if receipt["contract"] != _COMPLETION_CONTRACT or receipt["version"] != _COMPLETION_VERSION:
        raise ConflictError("Stage 12D completion receipt contract is invalid")
    if receipt["plan_sha256"] != expectation.plan_sha256:
        raise ConflictError("Stage 12D completion receipt plan binding is invalid")
    if receipt["scope_manifest_sha256"] != expectation.scope_semantic_sha256:
        raise ConflictError("Stage 12D completion receipt scope binding is invalid")
    if (
        _require_int(receipt["published_unit_count"], "completion published count")
        != expectation.published_complete
        or _require_int(receipt["total_attempt_count"], "completion total attempts")
        != expectation.closed
        or _require_int(receipt["terminal_noncoverage_count"], "completion terminal count")
        != expectation.successful_empty + expectation.authorized_http_402
    ):
        raise ConflictError("Stage 12D completion receipt aggregate ledger is invalid")
    for key in (
        "baseline_sha256",
        "static_preflight_sha256",
    ):
        _require_sha256(receipt[key], f"completion {key}")
    if expectation.canonical:
        if expectation.fixture_id is not None:
            raise ValidationError("Stage 12D canonical completion fixture binding is invalid")
    elif (
        expectation.fixture_id is None
        or receipt["baseline_sha256"] != _fixture_completion_authority_sha256(expectation.fixture_id)
    ):
        raise ConflictError("Stage 12D fixture completion authority binding is invalid")
    results = receipt["result_sha256s"]
    if not isinstance(results, list) or len(results) != expectation.closed:
        raise ConflictError("Stage 12D completion receipt result digest list is invalid")
    for value in results:
        _require_sha256(value, "completion result digest")
    terminal_outcomes = _terminal_ledger(receipt["terminal_outcomes"], expectation)
    final_target = _completion_file_identity(receipt["final_target"], "completion final target")
    if (
        final_target["byte_count"] != target.size
        or final_target["identity_sha256"] != target.identity_sha256()
        or final_target["mtime_ns"] != target.mtime_ns
    ):
        raise ConflictError("Stage 12D completion receipt target identity is no longer current")
    _completion_file_identity(receipt["source_final"], "completion retained-source summary")
    bound_checks = _completion_checks(receipt["final_target_checks"], "completion target checks")
    if (
        bound_checks["current_price_rows"] != checks.current_rows
        or bound_checks["immutable_price_versions"] != checks.version_rows
        or bound_checks["daily_price_captures"] != checks.captures
        or bound_checks["scoped_current_row_count"] != checks.stage12c_current_rows
        or bound_checks["foreign_key_violation_count"] != checks.foreign_key_violations
        or bound_checks["stage10_current_duplicate_count"] != checks.duplicate_current
        or bound_checks["stage10_version_duplicate_count"] != checks.duplicate_versions
        or bound_checks["stage10_capture_duplicate_count"] != checks.duplicate_captures
        or bound_checks["stage10_current_pointer_anomaly_count"] != checks.pointer_anomalies
    ):
        raise ConflictError("Stage 12D completion receipt final check binding is invalid")
    return terminal_outcomes


def _scalar(
    connection: sqlite3.Connection, query: str, parameters: tuple[object, ...] = ()
) -> int:
    row = connection.execute(query, parameters).fetchone()
    if row is None:
        raise ValidationError("Stage 12D fixed query returned no row")
    return _require_int(row[0], "fixed query result")


def _canonical_published_symbols(
    scope: Stage12DMarketNoTransferAdoptionScope,
    completion: _CompletionExpectation,
    terminal_outcomes: tuple[Stage12DFixtureTerminalOutcome, ...],
) -> tuple[str, ...]:
    """Re-load the frozen Stage 12A--C source authority without opening a store."""

    stage12a_source = APPROVED_PROJECT_ROOT / "config" / "stage12_market_v1_scope.json"
    stage12b_source = APPROVED_PROJECT_ROOT / "config" / "stage12b_incremental_market_v1_scope.json"
    stage12c_source = APPROVED_PROJECT_ROOT / "config" / "stage12c_market_gap_v1_scope.json"
    stage12a_scope = load_stage12_market_v1_scope(stage12a_source)
    stage12b_scope = load_stage12b_incremental_market_v1_scope(stage12b_source)
    stage12c_scope = load_stage12c_market_gap_v1_scope(stage12c_source)
    ordered = stage12c_ordered_symbols(stage12c_scope, stage12a_scope, stage12b_scope)
    if (
        stage12c_scope.source_file_sha256 != scope.stage12c.scope_file_sha256
        or stage12c_scope.manifest_sha256 != scope.stage12c.scope_semantic_sha256
        or stage12c_scope.manifest_sha256 != completion.scope_semantic_sha256
        or len(ordered) != completion.closed
    ):
        raise ConflictError("Stage 12D frozen Stage 12C roster binding is invalid")
    terminal_symbols = {item.symbol for item in terminal_outcomes}
    if not terminal_symbols.issubset(set(ordered)):
        raise ConflictError("Stage 12D completion terminal symbol is outside the frozen roster")
    published = tuple(symbol for symbol in ordered if symbol not in terminal_symbols)
    if len(published) != completion.published_complete or _AAPL not in published:
        raise ConflictError("Stage 12D frozen Stage 12C published roster is invalid")
    return published


def _expected_published_symbols(
    scope: Stage12DMarketNoTransferAdoptionScope,
    expectations: _ProofExpectations,
    completion: _CompletionExpectation,
    terminal_outcomes: tuple[Stage12DFixtureTerminalOutcome, ...],
) -> tuple[str, ...]:
    if expectations.published_symbols is None:
        return _canonical_published_symbols(scope, completion, terminal_outcomes)
    published = expectations.published_symbols
    terminal_symbols = {item.symbol for item in terminal_outcomes}
    if (
        len(published) != completion.published_complete
        or len(set(published)) != len(published)
        or _AAPL not in published
        or terminal_symbols.intersection(published)
    ):
        raise ConflictError("Stage 12D fixture published coverage binding is invalid")
    return published


def _grouped_symbol_counts(
    connection: sqlite3.Connection,
    query: str,
    parameters: tuple[object, ...],
    *,
    label: str,
) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in connection.execute(query, parameters):
        if len(row) != 2:
            raise ValidationError(f"Stage 12D {label} query is invalid")
        symbol = _require_text(row[0], f"{label} symbol")
        if symbol in result:
            raise ConflictError(f"Stage 12D {label} query is duplicated")
        result[symbol] = _require_int(row[1], f"{label} count")
    return result


def _grouped_symbol_dates(
    connection: sqlite3.Connection,
    query: str,
    parameters: tuple[object, ...],
    *,
    label: str,
) -> dict[tuple[str, str], int]:
    result: dict[tuple[str, str], int] = {}
    for row in connection.execute(query, parameters):
        if len(row) != 3:
            raise ValidationError(f"Stage 12D {label} query is invalid")
        symbol = _require_text(row[0], f"{label} symbol")
        trade_date = _require_text(row[1], f"{label} trade date", maximum=10)
        key = (symbol, trade_date)
        if key in result:
            raise ConflictError(f"Stage 12D {label} query is duplicated")
        result[key] = _require_int(row[2], f"{label} count")
    return result


def _invoke_query_hook(
    hook: Callable[[str], None] | None,
    label: str,
    guard: _TargetGuard,
    before: Mapping[str, Stage12DFileStamp],
    target: Path,
    authority_recheck: Callable[[str], None] | None,
) -> None:
    if authority_recheck is not None:
        authority_recheck(f"before {label}")
    guard.recheck(f"before {label}")
    _require_stamps_unchanged(before, target, f"before {label}")
    if hook is not None:
        hook(label)
    if authority_recheck is not None:
        authority_recheck(f"during {label}")
    guard.recheck(f"during {label}")
    _require_stamps_unchanged(before, target, f"during {label}")


def _run_fixed_checks(
    connection: sqlite3.Connection,
    *,
    scope: Stage12DMarketNoTransferAdoptionScope,
    guard: _TargetGuard,
    before: Mapping[str, Stage12DFileStamp],
    target: Path,
    expectations: _ProofExpectations,
    completion: _CompletionExpectation,
    terminal_outcomes: tuple[Stage12DFixtureTerminalOutcome, ...],
    query_hook: Callable[[str], None] | None,
    authority_recheck: Callable[[str], None] | None = None,
) -> dict[str, object]:
    query_only = connection.execute("PRAGMA query_only").fetchone()
    if query_only is None or query_only[0] != 1:
        raise ConflictError("Stage 12D SQLite connection is not query-only")
    _invoke_query_hook(query_hook, "relations", guard, before, target, authority_recheck)
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if not _REQUIRED_TABLES.issubset(tables):
        raise ValidationError("Stage 12D market target lacks the reviewed price relations")
    _invoke_query_hook(query_hook, "integrity", guard, before, target, authority_recheck)
    integrity = tuple(str(row[0]) for row in connection.execute("PRAGMA integrity_check"))
    if integrity != ("ok",):
        raise ConflictError("Stage 12D market target integrity check failed")
    _invoke_query_hook(query_hook, "foreign_keys", guard, before, target, authority_recheck)
    foreign_key_violations = _scalar(connection, "SELECT count(*) FROM pragma_foreign_key_check")
    _invoke_query_hook(query_hook, "counts", guard, before, target, authority_recheck)
    current_rows = _scalar(connection, "SELECT count(*) FROM stage10_daily_prices")
    version_rows = _scalar(connection, "SELECT count(*) FROM stage10_daily_price_versions")
    captures = _scalar(connection, "SELECT count(*) FROM stage10_daily_price_captures")
    _invoke_query_hook(query_hook, "duplicates", guard, before, target, authority_recheck)
    duplicate_current = _scalar(
        connection,
        """
        SELECT count(*) FROM (
            SELECT instrument_id, trade_date, provider, price_variant, currency_segment
            FROM stage10_daily_prices
            GROUP BY instrument_id, trade_date, provider, price_variant, currency_segment
            HAVING count(*) > 1
        )
        """,
    )
    duplicate_versions = _scalar(
        connection,
        """
        SELECT count(*) FROM (
            SELECT instrument_id, trade_date, provider, price_variant, currency_segment,
                   correction_sequence
            FROM stage10_daily_price_versions
            GROUP BY instrument_id, trade_date, provider, price_variant, currency_segment,
                     correction_sequence
            HAVING count(*) > 1
        )
        """,
    )
    duplicate_captures = _scalar(
        connection,
        """
        SELECT count(*) FROM (
            SELECT instrument_id, response_sha256
            FROM stage10_daily_price_captures
            GROUP BY instrument_id, response_sha256
            HAVING count(*) > 1
        )
        """,
    )
    _invoke_query_hook(query_hook, "pointers", guard, before, target, authority_recheck)
    pointer_anomalies = _scalar(
        connection,
        """
        SELECT count(*)
        FROM stage10_daily_prices AS current
        LEFT JOIN stage10_daily_price_versions AS version
          ON version.version_id=current.current_version_id
        WHERE version.version_id IS NULL
           OR version.instrument_id != current.instrument_id
           OR version.trade_date != current.trade_date
           OR version.provider != current.provider
           OR version.price_variant != current.price_variant
           OR version.currency_segment != current.currency_segment
           OR EXISTS (
               SELECT 1
               FROM stage10_daily_price_versions AS later
               WHERE later.instrument_id=current.instrument_id
                 AND later.trade_date=current.trade_date
                 AND later.provider=current.provider
                 AND later.price_variant=current.price_variant
                 AND later.currency_segment=current.currency_segment
                 AND later.correction_sequence > version.correction_sequence
           )
        """,
    )
    _invoke_query_hook(query_hook, "stage12c_scope", guard, before, target, authority_recheck)
    scope_digest = completion.scope_semantic_sha256
    published_symbols = _expected_published_symbols(
        scope, expectations, completion, terminal_outcomes
    )
    expected_captures = {symbol: 1 for symbol in published_symbols}
    expected_dates = {
        (symbol, trade_date): 1
        for symbol in published_symbols
        for trade_date in _SESSION_DATES
    }
    capture_by_symbol = _grouped_symbol_counts(
        connection,
        """
        SELECT capture.provider_symbol, count(*)
        FROM stage10_daily_price_captures AS capture
        WHERE capture.scope_manifest_sha256=?
        GROUP BY capture.provider_symbol
        """,
        (scope_digest,),
        label="Stage 12C capture coverage",
    )
    version_by_symbol_date = _grouped_symbol_dates(
        connection,
        """
        SELECT capture.provider_symbol, version.trade_date, count(*)
        FROM stage10_daily_price_versions AS version
        JOIN stage10_daily_price_captures AS capture ON capture.capture_id=version.capture_id
        WHERE capture.scope_manifest_sha256=?
        GROUP BY capture.provider_symbol, version.trade_date
        """,
        (scope_digest,),
        label="Stage 12C version coverage",
    )
    current_by_symbol_date = _grouped_symbol_dates(
        connection,
        """
        SELECT capture.provider_symbol, current.trade_date, count(*)
        FROM stage10_daily_prices AS current
        JOIN stage10_daily_price_versions AS version ON version.version_id=current.current_version_id
        JOIN stage10_daily_price_captures AS capture ON capture.capture_id=version.capture_id
        WHERE capture.scope_manifest_sha256=?
        GROUP BY capture.provider_symbol, current.trade_date
        """,
        (scope_digest,),
        label="Stage 12C current coverage",
    )
    terminal_symbols = {item.symbol for item in terminal_outcomes}
    terminal_database_facts = (
        sum(capture_by_symbol.get(symbol, 0) for symbol in terminal_symbols)
        + sum(
            count
            for (symbol, _), count in version_by_symbol_date.items()
            if symbol in terminal_symbols
        )
        + sum(
            count
            for (symbol, _), count in current_by_symbol_date.items()
            if symbol in terminal_symbols
        )
    )
    if terminal_database_facts != 0:
        raise ConflictError("Stage 12D terminal Stage 12C outcome has persisted database facts")
    if (
        capture_by_symbol != expected_captures
        or version_by_symbol_date != expected_dates
        or current_by_symbol_date != expected_dates
    ):
        raise ConflictError("Stage 12D Stage 12C scoped published coverage is incomplete or substituted")
    stage12c_captures = sum(capture_by_symbol.values())
    stage12c_versions = sum(version_by_symbol_date.values())
    stage12c_current = sum(current_by_symbol_date.values())
    stage12c_correction_anomalies = _scalar(
        connection,
        """
        SELECT count(*)
        FROM stage10_daily_price_versions AS version
        JOIN stage10_daily_price_captures AS capture ON capture.capture_id=version.capture_id
        WHERE capture.scope_manifest_sha256=?
          AND (
              version.correction_sequence != 1
              OR version.supersedes_version_id IS NOT NULL
          )
        """,
        (scope_digest,),
    )
    stage12c_availability_anomalies = _scalar(
        connection,
        """
        SELECT count(*)
        FROM stage10_daily_price_versions AS version
        JOIN stage10_daily_price_captures AS capture ON capture.capture_id=version.capture_id
        WHERE capture.scope_manifest_sha256=?
          AND version.available_at != version.captured_at
        """,
        (scope_digest,),
    )
    _invoke_query_hook(query_hook, "aapl", guard, before, target, authority_recheck)
    aapl_by_date = _grouped_symbol_dates(
        connection,
        """
        SELECT capture.provider_symbol, current.trade_date, count(*)
        FROM stage10_daily_prices AS current
        JOIN stage10_daily_price_versions AS version ON version.version_id=current.current_version_id
        JOIN stage10_daily_price_captures AS capture ON capture.capture_id=version.capture_id
        WHERE capture.scope_manifest_sha256=?
          AND capture.provider_symbol=?
        GROUP BY capture.provider_symbol, current.trade_date
        """,
        (scope_digest, _AAPL),
        label="Stage 12C AAPL coverage",
    )
    expected_aapl = {(_AAPL, trade_date): 1 for trade_date in _SESSION_DATES}
    if _AAPL not in published_symbols or aapl_by_date != expected_aapl:
        raise ConflictError("Stage 12D Stage 12C AAPL scoped coverage is invalid")
    aapl_current = sum(aapl_by_date.values())
    actual = {
        "aapl_current_rows": aapl_current,
        "captures": captures,
        "current_rows": current_rows,
        "duplicate_captures": duplicate_captures,
        "duplicate_current": duplicate_current,
        "duplicate_versions": duplicate_versions,
        "foreign_key_violations": foreign_key_violations,
        "integrity": "ok",
        "pointer_anomalies": pointer_anomalies,
        "stage12c_availability_anomalies": stage12c_availability_anomalies,
        "stage12c_captures": stage12c_captures,
        "stage12c_correction_anomalies": stage12c_correction_anomalies,
        "stage12c_current_rows": stage12c_current,
        "stage12c_version_rows": stage12c_versions,
        "terminal_database_facts": terminal_database_facts,
        "version_rows": version_rows,
    }
    expected = _expected_check_results(expectations)
    if actual != expected:
        raise ConflictError("Stage 12D market target checks differ from frozen expectations")
    if authority_recheck is not None:
        authority_recheck("after fixed queries")
    guard.recheck("after fixed queries")
    _require_stamps_unchanged(before, target, "after fixed queries")
    return actual


def _open_immutable_connection(guard: _TargetGuard) -> sqlite3.Connection:
    uri = f"file:/proc/self/fd/{guard.descriptor}?mode=ro&immutable=1"
    try:
        connection = sqlite3.connect(
            uri,
            uri=True,
            timeout=0.0,
            isolation_level=None,
        )
        connection.execute("PRAGMA query_only=ON")
        row = connection.execute("PRAGMA query_only").fetchone()
        if row is None or row[0] != 1:
            connection.close()
            raise ConflictError("Stage 12D immutable SQLite connection is not query-only")
        return connection
    except ConflictError:
        raise
    except sqlite3.Error as exc:
        raise StoreUnavailableError("Stage 12D immutable descriptor SQLite URI is unsupported") from exc


def _receipt_semantic_material(
    *,
    scope: Stage12DMarketNoTransferAdoptionScope,
    completion: _CompletionExpectation,
    checks: Mapping[str, object],
) -> dict[str, object]:
    """Return only stable logical proof facts.

    Physical stamps remain in the immutable receipt and are compared exactly
    around every read, but their device/inode/timestamp values must not make
    separately invoked proofs semantically unequal.
    """

    return {
        "access": {
            "connection_uri_query": scope.access.connection_uri_query,
            "query_only": scope.access.query_only,
        },
        "checks": dict(checks),
        "completion": _semantic_completion_authority(completion),
        "contract": scope.contract,
        "contract_version": scope.version,
        "scope": {
            "manifest_sha256": scope.manifest_sha256,
            "registry_revision": scope.registry.revision,
            "schema_version": scope.registry.schema_version,
            "source_file_sha256": scope.source_file_sha256,
        },
    }


def _build_proof_receipt(
    *,
    scope: Stage12DMarketNoTransferAdoptionScope,
    completion: _CompletionExpectation,
    ordinal: int,
    completed_at: str,
    invocation_identity: str,
    main_stamp: Stage12DFileStamp,
    sidecars: Mapping[str, Stage12DFileStamp],
    checks: Mapping[str, object],
) -> tuple[dict[str, object], str]:
    semantic = _receipt_semantic_material(
        scope=scope,
        completion=completion,
        checks=checks,
    )
    semantic_sha256 = _sha256_json(semantic)
    material: dict[str, object] = {
        "checks": dict(checks),
        "contract": scope.contract,
        "invocation": {
            "completed_at": completed_at,
            "identity": invocation_identity,
            "ordinal": ordinal,
        },
        "receipt_schema": STAGE12D_RECEIPT_SCHEMA,
        "scope": {
            "manifest_sha256": scope.manifest_sha256,
            "registry_revision": scope.registry.revision,
            "schema_version": scope.registry.schema_version,
            "source_file_sha256": scope.source_file_sha256,
        },
        "semantic_proof_sha256": semantic_sha256,
        "sidecars": {
            "journal": {
                "post_stamp": sidecars["journal"].manifest_mapping(),
                "pre_stamp": sidecars["journal"].manifest_mapping(),
            },
            "shm": {
                "post_stamp": sidecars["shm"].manifest_mapping(),
                "pre_stamp": sidecars["shm"].manifest_mapping(),
            },
            "wal": {
                "post_stamp": sidecars["wal"].manifest_mapping(),
                "pre_stamp": sidecars["wal"].manifest_mapping(),
            },
        },
        "stage12c_binding": {
            "authorized_http_402": completion.authorized_http_402,
            "closed": completion.closed,
            "completion_receipt_sha256": completion.receipt_sha256,
            "plan_sha256": completion.plan_sha256,
            "published_complete": completion.published_complete,
            "scope_semantic_sha256": completion.scope_semantic_sha256,
            "successful_empty": completion.successful_empty,
        },
        "target": {
            "post_stamp": main_stamp.manifest_mapping(),
            "pre_stamp": main_stamp.manifest_mapping(),
            "project_relative_path": scope.target.project_relative_path,
        },
        "version": scope.version,
    }
    receipt = {**material, "sha256": _sha256_json(material)}
    return receipt, semantic_sha256


def _proof_material(receipt: Mapping[str, object]) -> dict[str, object]:
    expected = {
        "checks",
        "contract",
        "invocation",
        "receipt_schema",
        "scope",
        "semantic_proof_sha256",
        "sha256",
        "sidecars",
        "stage12c_binding",
        "target",
        "version",
    }
    if set(receipt) != expected:
        raise ConflictError("Stage 12D proof receipt shape is invalid")
    return {key: value for key, value in receipt.items() if key != "sha256"}



def _validate_receipt_stamp(value: object, label: str) -> Mapping[str, object]:
    stamp = _mapping(value, label)
    if set(stamp) == {"exists"}:
        if stamp["exists"] is not False:
            raise ConflictError(f"Stage 12D {label} absent stamp is invalid")
        return stamp
    _exact_mapping_keys(
        stamp,
        frozenset(
            {
                "ctime_ns",
                "device",
                "exists",
                "inode",
                "kind",
                "link_count",
                "mode",
                "mtime_ns",
                "size",
            }
        ),
        label,
    )
    if stamp["exists"] is not True or stamp["kind"] != "regular":
        raise ConflictError(f"Stage 12D {label} regular stamp is invalid")
    for key in ("ctime_ns", "device", "inode", "mtime_ns", "size"):
        _require_int(stamp[key], f"{label} {key}")
    link_count = _require_int(stamp["link_count"], f"{label} link count", minimum=1)
    mode = _require_int(stamp["mode"], f"{label} mode")
    if link_count < 1 or not stat.S_ISREG(mode):
        raise ConflictError(f"Stage 12D {label} stamp is not a regular file")
    return stamp


def _validate_proof_receipt_stamps(
    receipt: Mapping[str, object],
    *,
    scope: Stage12DMarketNoTransferAdoptionScope,
    frozen_stamps: Mapping[str, Stage12DFileStamp],
) -> None:
    target = _mapping(receipt["target"], "proof target")
    _exact_mapping_keys(
        target,
        frozenset({"post_stamp", "pre_stamp", "project_relative_path"}),
        "proof target",
    )
    if target["project_relative_path"] != scope.target.project_relative_path:
        raise ConflictError("Stage 12D proof receipt target is cross-bound")
    target_pre = _validate_receipt_stamp(target["pre_stamp"], "proof target pre-stamp")
    target_post = _validate_receipt_stamp(target["post_stamp"], "proof target post-stamp")
    if (
        target_pre != target_post
        or target_pre.get("link_count") != 1
        or dict(target_pre) != frozen_stamps["main"].manifest_mapping()
    ):
        raise ConflictError("Stage 12D proof receipt target stamps are invalid")
    sidecars = _mapping(receipt["sidecars"], "proof sidecars")
    _exact_mapping_keys(sidecars, frozenset({"journal", "shm", "wal"}), "proof sidecars")
    for name in ("wal", "shm", "journal"):
        sidecar = _mapping(sidecars[name], f"proof {name} sidecar")
        _exact_mapping_keys(
            sidecar,
            frozenset({"post_stamp", "pre_stamp"}),
            f"proof {name} sidecar",
        )
        pre = _validate_receipt_stamp(sidecar["pre_stamp"], f"proof {name} pre-stamp")
        post = _validate_receipt_stamp(sidecar["post_stamp"], f"proof {name} post-stamp")
        if (
            pre != post
            or dict(pre) != frozen_stamps[name].manifest_mapping()
            or (name in {"wal", "journal"} and pre["exists"] and pre["size"] != 0)
        ):
            raise ConflictError(f"Stage 12D proof {name} sidecar stamp is invalid")


@dataclass(frozen=True, slots=True)
class _ValidatedProofReceipt:
    semantic_sha256: str
    invocation_identity: str
    receipt_sha256: str


def _validate_proof_receipt(
    receipt: Mapping[str, object],
    *,
    scope: Stage12DMarketNoTransferAdoptionScope,
    completion: _CompletionExpectation,
    expectations: _ProofExpectations,
    ordinal: int,
    receipt_name: str,
    frozen_stamps: Mapping[str, Stage12DFileStamp],
) -> _ValidatedProofReceipt:
    """Validate one immutable receipt before it can advance the proof sequence."""

    filename_match = _parse_proof_receipt_name(receipt_name)
    if filename_match[0] != ordinal:
        raise ConflictError("Stage 12D proof receipt filename ordinal is invalid")
    material = _proof_material(receipt)
    receipt_sha256 = _require_sha256(receipt["sha256"], "proof receipt digest")
    if (
        receipt["receipt_schema"] != STAGE12D_RECEIPT_SCHEMA
        or receipt["contract"] != scope.contract
        or receipt["version"] != scope.version
        or _sha256_json(material) != receipt_sha256
    ):
        raise ConflictError("Stage 12D proof receipt binding is invalid")
    invocation = _mapping(receipt["invocation"], "proof invocation")
    _exact_mapping_keys(invocation, frozenset({"completed_at", "identity", "ordinal"}), "proof invocation")
    if _require_int(invocation["ordinal"], "proof receipt ordinal", minimum=1) != ordinal:
        raise ConflictError("Stage 12D proof receipt ordinal is invalid")
    invocation_identity = _require_text(invocation["identity"], "proof receipt identity")
    if _INVOCATION_IDENTITY.fullmatch(invocation_identity) is None or invocation_identity != filename_match[1]:
        raise ConflictError("Stage 12D proof receipt filename identity is invalid")
    completed_at = _require_text(
        invocation["completed_at"], "proof receipt completion time", maximum=_UTC_MAXIMUM_LENGTH
    )
    if not completed_at.endswith("Z"):
        raise ConflictError("Stage 12D proof receipt completion time is not UTC")
    try:
        datetime.fromisoformat(completed_at[:-1] + "+00:00")
    except ValueError as exc:
        raise ConflictError("Stage 12D proof receipt completion time is invalid") from exc
    scope_mapping = _mapping(receipt["scope"], "proof scope")
    _exact_mapping_keys(
        scope_mapping,
        frozenset(
            {
                "manifest_sha256",
                "registry_revision",
                "schema_version",
                "source_file_sha256",
            }
        ),
        "proof scope",
    )
    if (
        scope_mapping["manifest_sha256"] != scope.manifest_sha256
        or scope_mapping["source_file_sha256"] != scope.source_file_sha256
        or scope_mapping["registry_revision"] != scope.registry.revision
        or scope_mapping["schema_version"] != scope.registry.schema_version
    ):
        raise ConflictError("Stage 12D proof receipt scope is cross-bound")
    _validate_proof_receipt_stamps(receipt, scope=scope, frozen_stamps=frozen_stamps)
    stage12c = _mapping(receipt["stage12c_binding"], "proof Stage 12C binding")
    _exact_mapping_keys(
        stage12c,
        frozenset(
            {
                "authorized_http_402",
                "closed",
                "completion_receipt_sha256",
                "plan_sha256",
                "published_complete",
                "scope_semantic_sha256",
                "successful_empty",
            }
        ),
        "proof Stage 12C binding",
    )
    if (
        _require_sha256(
            stage12c["completion_receipt_sha256"], "proof Stage 12C completion receipt digest"
        )
        != completion.receipt_sha256
        or _require_sha256(stage12c["plan_sha256"], "proof Stage 12C plan digest")
        != completion.plan_sha256
        or _require_sha256(stage12c["scope_semantic_sha256"], "proof Stage 12C scope digest")
        != completion.scope_semantic_sha256
        or _require_int(stage12c["published_complete"], "proof Stage 12C published count")
        != completion.published_complete
        or _require_int(stage12c["successful_empty"], "proof Stage 12C empty count")
        != completion.successful_empty
        or _require_int(stage12c["authorized_http_402"], "proof Stage 12C authorized count")
        != completion.authorized_http_402
        or _require_int(stage12c["closed"], "proof Stage 12C closed count") != completion.closed
    ):
        raise ConflictError("Stage 12D proof receipt Stage 12C binding is invalid")
    checks = _mapping(receipt["checks"], "proof checks")
    if dict(checks) != _expected_check_results(expectations):
        raise ConflictError("Stage 12D proof receipt check results are cross-bound")
    semantic = _require_sha256(receipt["semantic_proof_sha256"], "proof semantic digest")
    if (
        semantic
        != _sha256_json(
            _receipt_semantic_material(
                scope=scope,
                completion=completion,
                checks=checks,
            )
        )
    ):
        raise ConflictError("Stage 12D proof receipt semantic digest is invalid")
    return _ValidatedProofReceipt(
        semantic_sha256=semantic,
        invocation_identity=invocation_identity,
        receipt_sha256=receipt_sha256,
    )




@dataclass(frozen=True, slots=True)
class _ReceiptDirectoryIdentity:
    device: int
    inode: int
    mode: int
    owner: int


def _receipt_directory_identity_from_stat(
    info: os.stat_result, label: str
) -> _ReceiptDirectoryIdentity:
    if (
        not stat.S_ISDIR(info.st_mode)
        or int(info.st_uid) != os.getuid()
        or stat.S_IMODE(info.st_mode) != _ROOT_MODE
    ):
        raise ConflictError(f"Stage 12D {label} is not the reviewed private directory")
    return _ReceiptDirectoryIdentity(
        device=int(info.st_dev),
        inode=int(info.st_ino),
        mode=stat.S_IMODE(info.st_mode),
        owner=int(info.st_uid),
    )


def _receipt_directory_path_identity(root: Path, label: str) -> _ReceiptDirectoryIdentity:
    try:
        direct = root.lstat()
        followed = root.stat()
    except OSError as exc:
        raise StoreUnavailableError(f"Stage 12D {label} is unavailable") from exc
    if (
        stat.S_ISLNK(direct.st_mode)
        or not stat.S_ISDIR(direct.st_mode)
        or not stat.S_ISDIR(followed.st_mode)
        or int(direct.st_dev) != int(followed.st_dev)
        or int(direct.st_ino) != int(followed.st_ino)
    ):
        raise ConflictError(f"Stage 12D {label} must be a direct private directory")
    return _receipt_directory_identity_from_stat(direct, label)


@dataclass(slots=True)
class _ReceiptDirectoryGuard:
    root: Path
    descriptor: int
    initial_identity: _ReceiptDirectoryIdentity

    def recheck(self, boundary: str) -> None:
        current = _receipt_directory_path_identity(self.root, "private receipt root")
        try:
            opened = _receipt_directory_identity_from_stat(
                os.fstat(self.descriptor), "private receipt root"
            )
        except OSError as exc:
            raise StoreUnavailableError(
                "Stage 12D private receipt directory guard is unavailable"
            ) from exc
        if current != self.initial_identity or opened != self.initial_identity:
            raise ConflictError(f"Stage 12D private receipt root changed {boundary}")


@contextmanager
def _held_receipt_directory(root: Path):
    descriptor: int | None = None
    try:
        pre = _receipt_directory_path_identity(root, "private receipt root")
        descriptor = os.open(
            root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | _require_no_follow_flag(),
        )
        opened = _receipt_directory_identity_from_stat(
            os.fstat(descriptor), "private receipt root"
        )
        post = _receipt_directory_path_identity(root, "private receipt root")
        if pre != opened or pre != post:
            raise ConflictError("Stage 12D private receipt root changed while opening")
        locked = _ReceiptDirectoryGuard(root, descriptor, pre)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked.recheck("after locking")
    except BlockingIOError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise ConflictError("Stage 12D proof receipt directory is already active") from exc
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        if exc.errno == errno.ELOOP:
            raise ConflictError("Stage 12D private receipt root must not be a symlink") from exc
        raise StoreUnavailableError("Stage 12D private receipt root cannot be locked") from exc
    try:
        yield locked
    finally:
        try:
            fcntl.flock(locked.descriptor, fcntl.LOCK_UN)
        finally:
            os.close(locked.descriptor)


def _sync_locked_receipt_directory(directory: _ReceiptDirectoryGuard, boundary: str) -> None:
    directory.recheck(f"before {boundary}")
    try:
        os.fsync(directory.descriptor)
    except OSError as exc:
        raise StoreUnavailableError(
            "Stage 12D private receipt directory cannot be synchronized"
        ) from exc
    directory.recheck(f"after {boundary}")


def _strict_receipt_stamp_from_stat(info: os.stat_result, label: str) -> Stage12DFileStamp:
    stamp = Stage12DFileStamp.from_stat(info)
    if (
        not stat.S_ISREG(info.st_mode)
        or int(info.st_uid) != os.getuid()
        or stamp.link_count != 1
        or stat.S_IMODE(info.st_mode) != _RECEIPT_MODE
    ):
        raise ConflictError(f"Stage 12D {label} must be one private regular receipt")
    return stamp


def _receipt_stamp_at(
    directory: _ReceiptDirectoryGuard, name: str, label: str
) -> Stage12DFileStamp:
    directory.recheck(f"before {label}")
    try:
        info = os.stat(name, dir_fd=directory.descriptor, follow_symlinks=False)
    except FileNotFoundError as exc:
        raise ConflictError(f"Stage 12D {label} is missing") from exc
    except OSError as exc:
        raise StoreUnavailableError(f"Stage 12D {label} cannot be inspected") from exc
    stamp = _strict_receipt_stamp_from_stat(info, label)
    directory.recheck(f"after {label}")
    return stamp


def _same_receipt_inode(left: Stage12DFileStamp, right: Stage12DFileStamp) -> bool:
    return (
        left.exists
        and right.exists
        and left.device == right.device
        and left.inode == right.inode
    )


@dataclass(slots=True)
class _ReceiptJsonGuard:
    directory: _ReceiptDirectoryGuard
    name: str
    label: str
    descriptor: int
    initial_stamp: Stage12DFileStamp
    payload: bytes
    parsed: Mapping[str, object]

    def recheck(self, boundary: str) -> None:
        self.directory.recheck(f"before {self.label} {boundary}")
        try:
            opened = _strict_receipt_stamp_from_stat(os.fstat(self.descriptor), self.label)
        except OSError as exc:
            raise StoreUnavailableError(f"Stage 12D {self.label} guard is unavailable") from exc
        current = _receipt_stamp_at(self.directory, self.name, self.label)
        if not _same_identity(self.initial_stamp, opened) or not _same_identity(
            self.initial_stamp, current
        ):
            raise ConflictError(f"Stage 12D {self.label} changed {boundary}")
        payload = _read_descriptor_payload(
            self.descriptor,
            self.initial_stamp,
            self.label,
            maximum=_MAX_PROOF_RECEIPT_BYTES,
        )
        if payload != self.payload or _sha256_bytes(payload) != _sha256_bytes(self.payload):
            raise ConflictError(f"Stage 12D {self.label} content changed {boundary}")
        self.directory.recheck(f"after {self.label} {boundary}")

    def close(self) -> None:
        try:
            os.close(self.descriptor)
        except OSError as exc:
            raise StoreUnavailableError(f"Stage 12D {self.label} guard cannot close") from exc


def _open_receipt_json_guard(
    directory: _ReceiptDirectoryGuard, name: str, label: str
) -> _ReceiptJsonGuard:
    _parse_proof_receipt_name(name)
    pre = _receipt_stamp_at(directory, name, label)
    descriptor: int | None = None
    try:
        directory.recheck(f"before opening {label}")
        descriptor = os.open(name, _open_flags(), dir_fd=directory.descriptor)
        opened = _strict_receipt_stamp_from_stat(os.fstat(descriptor), label)
        post = _receipt_stamp_at(directory, name, label)
        if not _same_identity(pre, opened) or not _same_identity(pre, post):
            raise ConflictError(f"Stage 12D {label} changed while opening")
        payload = _read_descriptor_payload(
            descriptor, opened, label, maximum=_MAX_PROOF_RECEIPT_BYTES
        )
        parsed = loads_strict(payload, max_bytes=_MAX_PROOF_RECEIPT_BYTES)
        if not isinstance(parsed, Mapping) or not all(isinstance(key, str) for key in parsed):
            raise ValidationError(f"Stage 12D {label} must contain an object")
        result = _ReceiptJsonGuard(
            directory, name, label, descriptor, opened, payload, parsed
        )
        result.recheck("after opening")
        descriptor = None
        return result
    except FileNotFoundError as exc:
        raise ConflictError(f"Stage 12D {label} is missing") from exc
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ConflictError(f"Stage 12D {label} must not be a symlink") from exc
        raise StoreUnavailableError(f"Stage 12D {label} cannot be read") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


@dataclass(frozen=True, slots=True)
class _CreatedReceipt:
    name: str
    receipt_sha256: str
    payload: bytes
    stamp: Stage12DFileStamp


def _write_receipt_chunk(descriptor: int, payload: bytes) -> int:
    """Narrow failure seam: callers never supply a path or receipt directory."""

    return os.write(descriptor, payload)


def _remove_created_receipt_at(
    directory: _ReceiptDirectoryGuard, created: _CreatedReceipt
) -> None:
    """Rollback only the exact inode using the original locked directory fd."""

    try:
        info = os.stat(
            created.name, dir_fd=directory.descriptor, follow_symlinks=False
        )
        current = _strict_receipt_stamp_from_stat(info, "new proof receipt")
        if not _same_receipt_inode(current, created.stamp):
            raise ConflictError("Stage 12D new proof receipt changed before rollback")
        os.unlink(created.name, dir_fd=directory.descriptor)
        os.fsync(directory.descriptor)
        try:
            os.stat(created.name, dir_fd=directory.descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return
        raise ConflictError("Stage 12D new proof receipt remains after rollback")
    except ConflictError:
        raise
    except OSError as exc:
        raise ConflictError("Stage 12D new proof receipt cannot be safely rolled back") from exc


def _write_exclusive_receipt_at(
    directory: _ReceiptDirectoryGuard,
    name: str,
    receipt: Mapping[str, object],
) -> _CreatedReceipt:
    _parse_proof_receipt_name(name)
    payload = dumps_strict(dict(receipt)).encode("utf-8")
    if len(payload) > _MAX_PROOF_RECEIPT_BYTES:
        raise ResourceLimitError("Stage 12D proof receipt exceeds the reviewed byte bound")
    descriptor: int | None = None
    created: _CreatedReceipt | None = None
    try:
        directory.recheck("before proof receipt creation")
        descriptor = os.open(
            name,
            _open_flags(write=True, create=True, exclusive=True),
            _RECEIPT_MODE,
            dir_fd=directory.descriptor,
        )
        os.fchmod(descriptor, _RECEIPT_MODE)
        initial = _strict_receipt_stamp_from_stat(
            os.fstat(descriptor), "new proof receipt"
        )
        created = _CreatedReceipt(
            name,
            _require_sha256(receipt.get("sha256"), "proof receipt digest"),
            payload,
            initial,
        )
        offset = 0
        while offset < len(payload):
            written = _write_receipt_chunk(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("short Stage 12D receipt write")
            offset += written
        os.fsync(descriptor)
        final = _strict_receipt_stamp_from_stat(os.fstat(descriptor), "new proof receipt")
        if not _same_receipt_inode(initial, final) or final.size != len(payload):
            raise ConflictError("Stage 12D proof receipt identity is unsafe")
        created = _CreatedReceipt(name, created.receipt_sha256, payload, final)
        os.close(descriptor)
        descriptor = None
        _sync_locked_receipt_directory(directory, "proof receipt creation")
        return created
    except FileExistsError as exc:
        raise ConflictError("Stage 12D immutable proof receipt already exists") from exc
    except BaseException:
        if descriptor is not None:
            try:
                if created is None:
                    try:
                        created = _CreatedReceipt(
                            name,
                            "",
                            b"",
                            _strict_receipt_stamp_from_stat(
                                os.fstat(descriptor), "new proof receipt"
                            ),
                        )
                    except OSError:
                        pass
                os.close(descriptor)
            finally:
                descriptor = None
        if created is not None:
            _remove_created_receipt_at(directory, created)
        raise


@dataclass(slots=True)
class _PriorProof:
    guard: _ReceiptJsonGuard
    validated: _ValidatedProofReceipt


@dataclass(slots=True)
class _NextProofState:
    ordinal: int
    prior: _PriorProof | None


def _next_proof_ordinal(
    directory: _ReceiptDirectoryGuard,
    *,
    scope: Stage12DMarketNoTransferAdoptionScope,
    completion: _CompletionExpectation,
    expectations: _ProofExpectations,
    frozen_stamps: Mapping[str, Stage12DFileStamp],
) -> _NextProofState:
    directory.recheck("before proof receipt listing")
    try:
        entries = tuple(os.listdir(directory.descriptor))
    except OSError as exc:
        raise StoreUnavailableError("Stage 12D private receipt root cannot be inspected") from exc
    directory.recheck("after proof receipt listing")
    by_ordinal: dict[int, str] = {}
    for entry in entries:
        ordinal, _ = _parse_proof_receipt_name(entry)
        if ordinal in by_ordinal:
            raise ConflictError("Stage 12D private receipt root has duplicate proof ordinals")
        by_ordinal[ordinal] = entry
    first_name = by_ordinal.get(1)
    second_name = by_ordinal.get(2)
    if second_name is not None and first_name is None:
        raise ConflictError("Stage 12D second proof receipt exists without a valid first receipt")
    if first_name is None:
        return _NextProofState(ordinal=1, prior=None)
    first_guard = _open_receipt_json_guard(directory, first_name, "first proof receipt")
    try:
        first = _validate_proof_receipt(
            first_guard.parsed,
            scope=scope,
            completion=completion,
            expectations=expectations,
            ordinal=1,
            receipt_name=first_name,
            frozen_stamps=frozen_stamps,
        )
        first_guard.recheck("after validation")
        if second_name is None:
            return _NextProofState(2, _PriorProof(first_guard, first))
        second_guard = _open_receipt_json_guard(directory, second_name, "second proof receipt")
        try:
            second = _validate_proof_receipt(
                second_guard.parsed,
                scope=scope,
                completion=completion,
                expectations=expectations,
                ordinal=2,
                receipt_name=second_name,
                frozen_stamps=frozen_stamps,
            )
            second_guard.recheck("after validation")
            if (
                second.semantic_sha256 != first.semantic_sha256
                or second.invocation_identity == first.invocation_identity
                or second.receipt_sha256 == first.receipt_sha256
            ):
                raise ConflictError("Stage 12D existing proof receipts disagree")
        finally:
            second_guard.close()
        raise ConflictError("Stage 12D permits exactly two successful proof receipts")
    except Exception:
        first_guard.close()
        raise


class _Stage12DProofEngine:
    """Shared fixed-target proof engine; only wrappers select its authority."""

    def __init__(
        self,
        *,
        project_root: Path,
        scope: Stage12DMarketNoTransferAdoptionScope,
        expectations: _ProofExpectations,
        completion: _CompletionExpectation,
        utcnow: Callable[[], datetime],
        hooks: Stage12DFixtureHooks | None,
    ) -> None:
        self._project_root = project_root
        self._scope = scope
        self._expectations = expectations
        self._completion = completion
        self._utcnow = utcnow
        self._hooks = hooks

    def run(self) -> Stage12DMarketProofReport:
        scope = require_stage12d_market_no_transfer_adoption_scope(self._scope)
        if (
            scope.target.project_relative_path != APPROVED_MARKET_RELATIVE_PATH.as_posix()
            or scope.stage12c.completion_receipt_path
            != APPROVED_STAGE12C_COMPLETION_RELATIVE_PATH.as_posix()
            or scope.receipt_policy.private_receipt_root
            != APPROVED_STAGE12D_PRIVATE_RELATIVE_ROOT.as_posix()
            or scope.receipt_policy.max_proofs != 2
            or scope.access.connection_uri_query != "mode=ro&immutable=1"
            or not scope.access.query_only
        ):
            raise ValidationError("Stage 12D scope does not bind the fixed proof protocol")
        root = _ensure_private_receipt_root(
            self._project_root, Path(scope.receipt_policy.private_receipt_root)
        )
        target = _require_relative_components(
            self._project_root,
            Path(scope.target.project_relative_path),
            "market target",
        )
        completion_path = _require_relative_components(
            self._project_root,
            Path(scope.stage12c.completion_receipt_path),
            "Stage 12C completion receipt",
        )
        with _held_receipt_directory(root) as receipt_directory:
            target_guard = _TargetGuard.open(target)
            completion_guard: _DirectJsonGuard | None = None
            next_state: _NextProofState | None = None
            created: _CreatedReceipt | None = None
            new_guard: _ReceiptJsonGuard | None = None
            try:
                before = _sidecar_stamps(target)
                target_guard.recheck("before completion receipt")
                completion_guard = _open_direct_json_guard(
                    completion_path,
                    "Stage 12C completion receipt",
                    maximum=_MAX_COMPLETION_BYTES,
                    private_receipt=True,
                )
                terminal_outcomes = _validate_completion_receipt(
                    completion_guard.parsed,
                    completion_guard.payload,
                    expectation=self._completion,
                    target=target_guard.initial_stamp,
                    checks=self._expectations,
                )
                completion_guard.recheck("after completion validation")
                target_guard.recheck("after completion receipt")
                _require_stamps_unchanged(before, target, "after completion receipt")
                next_state = _next_proof_ordinal(
                    receipt_directory,
                    scope=scope,
                    completion=self._completion,
                    expectations=self._expectations,
                    frozen_stamps=before,
                )

                def recheck_authority(boundary: str) -> None:
                    if completion_guard is None:
                        raise ConflictError("Stage 12D completion authority guard is unavailable")
                    completion_guard.recheck(boundary)
                    if next_state is not None and next_state.prior is not None:
                        next_state.prior.guard.recheck(boundary)

                recheck_authority("before connect")
                if self._hooks is not None and self._hooks.before_connect is not None:
                    self._hooks.before_connect()
                recheck_authority("after before-connect hook")
                target_guard.recheck("before immutable SQLite connection")
                _require_stamps_unchanged(before, target, "before immutable SQLite connection")
                connection = _open_immutable_connection(target_guard)
                try:
                    recheck_authority("after immutable SQLite connection")
                    target_guard.recheck("after immutable SQLite connection")
                    _require_stamps_unchanged(before, target, "after immutable SQLite connection")
                    checks = _run_fixed_checks(
                        connection,
                        scope=scope,
                        guard=target_guard,
                        before=before,
                        target=target,
                        expectations=self._expectations,
                        completion=self._completion,
                        terminal_outcomes=terminal_outcomes,
                        query_hook=None if self._hooks is None else self._hooks.before_query,
                        authority_recheck=recheck_authority,
                    )
                finally:
                    connection.close()
                recheck_authority("after immutable SQLite close")
                target_guard.recheck("after immutable SQLite close")
                after = _require_stamps_unchanged(before, target, "after immutable SQLite close")
                if self._hooks is not None and self._hooks.before_receipt is not None:
                    self._hooks.before_receipt()
                recheck_authority("before proof receipt")
                target_guard.recheck("before proof receipt")
                _require_stamps_unchanged(before, target, "before proof receipt")
                invocation_identity = uuid.uuid4().hex
                if _INVOCATION_IDENTITY.fullmatch(invocation_identity) is None:
                    raise ConflictError("Stage 12D generated proof identity is invalid")
                if (
                    next_state.prior is not None
                    and invocation_identity == next_state.prior.validated.invocation_identity
                ):
                    raise ConflictError("Stage 12D proof invocation identity is not unique")
                receipt, semantic = _build_proof_receipt(
                    scope=scope,
                    completion=self._completion,
                    ordinal=next_state.ordinal,
                    completed_at=_utc_text(self._utcnow()),
                    invocation_identity=invocation_identity,
                    main_stamp=target_guard.initial_stamp,
                    sidecars=after,
                    checks=checks,
                )
                if (
                    next_state.prior is not None
                    and semantic != next_state.prior.validated.semantic_sha256
                ):
                    raise ConflictError("Stage 12D proof semantic evidence differs from proof 1")
                receipt_name = _proof_receipt_name(next_state.ordinal, invocation_identity)
                created = _write_exclusive_receipt_at(receipt_directory, receipt_name, receipt)
                new_guard = _open_receipt_json_guard(
                    receipt_directory, receipt_name, "new proof receipt"
                )
                if new_guard.payload != created.payload:
                    raise ConflictError("Stage 12D newly written proof receipt changed")
                persisted = _validate_proof_receipt(
                    new_guard.parsed,
                    scope=scope,
                    completion=self._completion,
                    expectations=self._expectations,
                    ordinal=next_state.ordinal,
                    receipt_name=receipt_name,
                    frozen_stamps=before,
                )
                if (
                    persisted.receipt_sha256 != created.receipt_sha256
                    or persisted.semantic_sha256 != semantic
                    or (
                        next_state.prior is not None
                        and (
                            persisted.invocation_identity
                            == next_state.prior.validated.invocation_identity
                            or persisted.receipt_sha256
                            == next_state.prior.validated.receipt_sha256
                        )
                    )
                ):
                    raise ConflictError("Stage 12D proof receipt identity is invalid")
                if self._hooks is not None and self._hooks.after_receipt is not None:
                    self._hooks.after_receipt()
                recheck_authority("after proof receipt")
                new_guard.recheck("after proof receipt")
                target_guard.recheck("after proof receipt")
                _require_stamps_unchanged(before, target, "after proof receipt")
                recheck_authority("immediately before success")
                new_guard.recheck("immediately before success")
                target_guard.recheck("immediately before success")
                _require_stamps_unchanged(before, target, "immediately before success")
                return Stage12DMarketProofReport(
                    proof_ordinal=next_state.ordinal,
                    receipt_sha256=created.receipt_sha256,
                    semantic_proof_sha256=semantic,
                    scope_manifest_sha256=scope.manifest_sha256,
                    completion_receipt_sha256=self._completion.receipt_sha256,
                    target_identity_sha256=target_guard.initial_stamp.identity_sha256(),
                )
            except BaseException:
                if new_guard is not None:
                    try:
                        new_guard.close()
                    finally:
                        new_guard = None
                if created is not None:
                    _remove_created_receipt_at(receipt_directory, created)
                    created = None
                raise
            finally:
                if new_guard is not None:
                    new_guard.close()
                if next_state is not None and next_state.prior is not None:
                    next_state.prior.guard.close()
                if completion_guard is not None:
                    completion_guard.close()
                target_guard.close()


class Stage12DMarketProofRunner:
    """Public fixture-only no-transfer proof runner.

    The target and completion paths always come from the revalidated scope.
    The supplied root must be a direct strict child of the system temporary
    directory; callers cannot pass a target, retained source, SQL, credential,
    transport, backup, or environment object.
    """

    def __init__(
        self,
        *,
        project_root: str | Path,
        scope: object,
        expectations: Stage12DFixtureExpectations,
        completion: Stage12DFixtureCompletion,
        utcnow: Callable[[], datetime] | None = None,
        hooks: Stage12DFixtureHooks | None = None,
    ) -> None:
        self._project_root = _require_fixture_project_root(project_root)
        self._scope = require_stage12d_market_no_transfer_adoption_scope(scope)
        self._expectations = _fixture_expectations(expectations)
        self._completion = _fixture_completion(completion)
        if self._completion.receipt_sha256 == self._scope.stage12c.completion_receipt_sha256:
            raise ValidationError("Stage 12D public runner rejects canonical completion authority")
        if (
            self._completion.plan_sha256 == self._scope.stage12c.plan_sha256
            or self._completion.scope_semantic_sha256 == self._scope.stage12c.scope_semantic_sha256
        ):
            raise ValidationError("Stage 12D public runner rejects canonical completion authority")
        if hooks is not None and not isinstance(hooks, Stage12DFixtureHooks):
            raise ValidationError("Stage 12D fixture hooks are invalid")
        self._hooks = hooks
        self._utcnow = (lambda: datetime.now(timezone.utc)) if utcnow is None else utcnow
        if not callable(self._utcnow):
            raise ValidationError("Stage 12D proof clock is invalid")

    def run(self) -> Stage12DMarketProofReport:
        return _Stage12DProofEngine(
            project_root=self._project_root,
            scope=self._scope,
            expectations=self._expectations,
            completion=self._completion,
            utcnow=self._utcnow,
            hooks=self._hooks,
        ).run()


def _canonical_stage12d_market_proof_runner() -> _Stage12DProofEngine:
    """Build the one canonical runner; intentionally accepts no caller input."""

    scope = load_stage12d_market_no_transfer_adoption_scope()
    _require_direct_directory(APPROVED_PROJECT_ROOT, "canonical project root")
    if APPROVED_RETAINED_SOURCE == APPROVED_PROJECT_ROOT / APPROVED_MARKET_RELATIVE_PATH:
        raise ConflictError("Stage 12D canonical retained source binding is invalid")
    return _Stage12DProofEngine(
        project_root=APPROVED_PROJECT_ROOT,
        scope=scope,
        expectations=_canonical_expectations(scope),
        completion=_canonical_completion(scope),
        utcnow=lambda: datetime.now(timezone.utc),
        hooks=None,
    )


def run_stage12d_market_no_transfer_proof() -> Stage12DMarketProofReport:
    """Run one canonical Stage 12D proof against only the reviewed target."""

    return _canonical_stage12d_market_proof_runner().run()


__all__ = (
    "APPROVED_MARKET_RELATIVE_PATH",
    "APPROVED_PROJECT_ROOT",
    "APPROVED_STAGE12C_COMPLETION_RELATIVE_PATH",
    "APPROVED_STAGE12D_PRIVATE_RELATIVE_ROOT",
    "Stage12DFileStamp",
    "Stage12DFixtureCompletion",
    "Stage12DFixtureExpectations",
    "Stage12DFixtureTerminalOutcome",
    "Stage12DFixtureHooks",
    "Stage12DMarketProofReport",
    "Stage12DMarketProofRunner",
    "run_stage12d_market_no_transfer_proof",
)
