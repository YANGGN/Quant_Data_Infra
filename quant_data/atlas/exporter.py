"""Deterministic, offline publication of the frozen Atlas JSON snapshot."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
import fcntl
import hashlib
import math
import os
import secrets
from pathlib import Path
import re
import shutil
import stat
import time
from typing import Any

from ..errors import ConflictError, ResourceLimitError, ValidationError
from ..json_codec import dumps_strict, loads_strict
from ..operations.backup import ReadOnlyCopyCohort, capture_readonly_copies
from ..stores import STORE_ROLES, StoreMap
from ..temporal import TemporalValue
from .contracts import AtlasProjection, AtlasPublication
from .projections import collect_atlas_projections
from .site_bundle import copy_site_bundle


_EXPORT_ID = "atlas.fixture_snapshot"
_ATTEMPT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_CODE_REVISION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,255}$")
_REVISION_ID = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_PUBLIC = frozenset(
    {
        "database_path",
        "filesystem_path",
        "credential",
        "sql",
        "raw_artifact",
        "private_receipt",
        "body",
        "summary",
        "source_url",
        "run_id",
        "artifact_id",
        "snapshot_id",
        "lock_key",
        "physical_path",
        "canonical_uri",
    }
)
_LABELS = {
    "market-prices": "Market prices",
    "gdp-vintages": "GDP vintages",
    "company-issuers": "Company issuers",
    "news-items": "News items",
}


_QUERY_BOUNDS = {
    "max_total_rows": 12_000,
    "max_bytes": 8 * 1024 * 1024,
    "max_runtime_seconds": 30,
}
_PUBLIC_MANIFEST_KEYS = frozenset(
    {
        "export_id",
        "revision_id",
        "generated_at",
        "cutoff",
        "semantic_dataset_id",
        "lifecycle",
        "completeness",
        "point_in_time",
        "registry",
        "contract",
        "provenance",
        "source_stores",
        "cross_store_atomic",
        "totals",
        "files",
        "datasets",
    }
)

def _plain(value: object) -> object:
    """Copy recursively frozen registry values into JSON-safe plain values."""

    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _strict_bytes(value: object) -> bytes:
    return dumps_strict(value).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _fsync_directory(path: Path) -> None:
    """Persist a directory entry after a file or tree promotion."""

    if path.is_symlink() or not path.is_dir():
        raise ValidationError("Atlas durability directory is unavailable or unsafe")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise ValidationError("Atlas directory durability flush failed") from exc



def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve(strict=False).relative_to(parent.resolve(strict=False))
    except ValueError:
        return False
    return True


def _safe_file_name(value: str) -> bool:
    return bool(_ATTEMPT_ID.fullmatch(value)) and value not in {".", ".."}


def _assert_no_symlink_components(path: Path, *, stop: Path | None = None) -> None:
    current = path
    limit = stop.resolve(strict=False) if stop is not None else None
    while True:
        # Path.exists() is false for a dangling link, but a dangling parent
        # remains a traversal boundary that must fail closed.
        if current.is_symlink():
            raise ValidationError("Atlas output paths cannot traverse symbolic links")
        if limit is not None and current.resolve(strict=False) == limit:
            break
        if current.parent == current:
            break
        current = current.parent


def _require_directory(path: Path) -> None:
    if path.exists():
        if path.is_symlink() or not path.is_dir():
            raise ValidationError("Atlas output directory is unavailable or unsafe")
        return
    path.mkdir(parents=True, exist_ok=False)
    os.chmod(path, 0o755)
    _fsync_directory(path.parent)


def _require_private_directory(path: Path) -> None:
    if path.exists():
        if path.is_symlink() or not path.is_dir():
            raise ValidationError("Atlas private directory is unavailable or unsafe")
    else:
        path.mkdir(parents=True, exist_ok=False, mode=0o700)
    try:
        os.chmod(path, 0o700)
        _fsync_directory(path.parent)
    except OSError as exc:
        raise ValidationError("Atlas private directory permission setup failed") from exc


def _require_exact_private_directory(path: Path) -> None:
    """Require a non-symlink private lock directory without repairing it."""

    _assert_no_symlink_components(path.parent)
    created = False
    try:
        path.mkdir(mode=0o700)
        created = True
    except FileExistsError:
        pass
    except OSError as exc:
        raise ValidationError("Atlas publication lock directory is unavailable") from exc
    try:
        state = os.lstat(path)
        if not stat.S_ISDIR(state.st_mode) or stat.S_IMODE(state.st_mode) != 0o700:
            raise ValidationError("Atlas publication lock directory is unsafe")
        _assert_no_symlink_components(path)
        if created:
            _fsync_directory(path.parent)
    except OSError as exc:
        raise ValidationError("Atlas publication lock directory is unavailable") from exc


def _make_private_tree(root: Path) -> None:
    if root.is_symlink() or not root.is_dir():
        raise ValidationError("Atlas private tree is unavailable or unsafe")
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise ValidationError("Atlas private tree cannot contain symbolic links")
        try:
            os.chmod(path, 0o700 if path.is_dir() else 0o600)
        except OSError as exc:
            raise ValidationError("Atlas private tree permission setup failed") from exc
    os.chmod(root, 0o700)


def _make_public_tree(root: Path) -> None:
    if root.is_symlink() or not root.is_dir():
        raise ValidationError("Atlas public tree is unavailable or unsafe")
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise ValidationError("Atlas public tree cannot contain symbolic links")
        try:
            os.chmod(path, 0o755 if path.is_dir() else 0o644)
        except OSError as exc:
            raise ValidationError("Atlas public tree permission setup failed") from exc
    os.chmod(root, 0o755)


def _write_new(path: Path, payload: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise ConflictError("Atlas output must not overwrite an existing file")
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_symlink_components(path.parent)
    try:
        with path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(path, 0o644)
        _fsync_directory(path.parent)
    except OSError as exc:
        raise ValidationError("Atlas output write failed") from exc


def _atomic_replace(path: Path, payload: bytes) -> None:
    """Atomically replace only a strict JSON pointer or private receipt."""

    _assert_no_symlink_components(path.parent)
    temporary = path.parent / (
        "." + path.name + "." + secrets.token_hex(16) + ".tmp"
    )
    try:
        _write_new(temporary, payload)
    except ConflictError:
        raise
    except BaseException:
        try:
            if temporary.is_file() and not temporary.is_symlink():
                temporary.unlink()
        except OSError:
            pass
        raise

    try:
        os.replace(temporary, path)
    except OSError as exc:
        try:
            if temporary.is_file() and not temporary.is_symlink():
                temporary.unlink()
        except OSError:
            pass
        raise ValidationError("Atlas atomic promotion failed") from exc

    # A successful rename is the externally visible commit point. Directory
    # fsync remains best-effort because surfacing its error would report a
    # failed publication after the new pointer is already visible. All
    # referenced revision and receipt material is durable before this point.
    try:
        _fsync_directory(path.parent)
    except (OSError, ValidationError):
        return


def _atomic_publish_new(path: Path, payload: bytes, *, mode: int) -> None:
    """Durably publish a no-overwrite receipt through a private temporary."""

    _assert_no_symlink_components(path.parent)
    if path.exists() or path.is_symlink():
        raise ConflictError("Atlas immutable receipt target already exists")
    temporary = path.parent / ("." + path.name + ".tmp")
    if temporary.exists() or temporary.is_symlink():
        raise ConflictError("Atlas atomic temporary target already exists")
    _write_new(temporary, payload)
    try:
        os.chmod(temporary, mode)
        os.link(temporary, path, follow_symlinks=False)
        _fsync_directory(path.parent)
    except FileExistsError as exc:
        raise ConflictError("Atlas immutable receipt target already exists") from exc
    except OSError as exc:
        raise ValidationError("Atlas immutable receipt publication failed") from exc
    finally:
        if temporary.exists() and not temporary.is_symlink():
            try:
                temporary.unlink()
                _fsync_directory(path.parent)
            except OSError as exc:
                raise ValidationError("Atlas immutable receipt cleanup failed") from exc



def _declaration(registry: object, export_id: str) -> object:
    export = getattr(registry, "export", None)
    if not callable(export):
        raise ValidationError("Atlas requires a registry export declaration")
    declaration = export(export_id)
    if (
        getattr(declaration, "id", None) != _EXPORT_ID
        or getattr(declaration, "kind", None) != "atlas_snapshot"
        or getattr(declaration, "format", None) != "json"
        or getattr(declaration, "optional_dependency", object()) is not None
        or getattr(declaration, "source_mode", None) != "online_backup"
    ):
        raise ValidationError("Only the frozen JSON Atlas fixture export is supported")
    source_stores = tuple(getattr(declaration, "source_stores", ()))
    if source_stores != tuple(role.value for role in STORE_ROLES):
        raise ValidationError("Atlas export source-store contract is incomplete")
    lifecycle = _plain(getattr(declaration, "lifecycle", {}))
    if lifecycle != {
        "mode": "manual_only",
        "fixture_only": True,
        "network": False,
        "hosting": False,
        "status": "fixture_validated",
    }:
        raise ValidationError("Atlas export lifecycle is not offline fixture-only")
    consistency = _plain(getattr(declaration, "consistency", {}))
    if not isinstance(consistency, dict) or consistency.get("cross_store_atomic") is not False:
        raise ValidationError("Atlas export must not claim a cross-store atomic snapshot")
    if consistency.get("source_copies") != "online_backup_per_store" or consistency.get("read_access") != "query_only":
        raise ValidationError("Atlas export copy-consistency contract drifted")
    staging = _plain(getattr(declaration, "staging", {}))
    if not isinstance(staging, dict) or staging.get("cleanup") != "exact_child_only":
        raise ValidationError("Atlas export staging contract drifted")
    benchmark = _plain(getattr(declaration, "benchmark", {}))
    if not isinstance(benchmark, dict) or benchmark.get("decision") != "not_required_json_atlas_snapshot":
        raise ValidationError("Atlas JSON benchmark decision drifted")
    if benchmark.get("parquet") != "not_adopted" or benchmark.get("duckdb") != "not_adopted":
        raise ValidationError("Atlas must not introduce optional analytical dependencies")
    return declaration


def validate_export_root(
    export_root: str | Path,
    registry: object,
    export_id: str = _EXPORT_ID,
) -> Path:
    """Validate an explicit, non-broad, unmanaged host-selected export root."""

    _declaration(registry, export_id)
    if export_root is None or (isinstance(export_root, str) and not export_root.strip()):
        raise ValidationError("Atlas export requires an explicit output root")
    try:
        supplied = Path(export_root).expanduser()
    except (TypeError, ValueError) as exc:
        raise ValidationError("Atlas export requires an explicit output root") from exc
    if not supplied.is_absolute():
        raise ValidationError("Atlas export root must be absolute")
    if supplied.is_symlink():
        raise ValidationError("Atlas export root cannot be a symbolic link")
    _assert_no_symlink_components(supplied)
    root = supplied.resolve(strict=False)
    project = Path(getattr(registry, "project_root", ".")).resolve(strict=False)
    broad = {Path(root.anchor), Path("/tmp"), Path.home().resolve(strict=False), project}
    protected = {
        project / ".git",
        project / "sites" / "quant-data-atlas",
        project / "data",
        project / "stores",
    }
    if root in broad or any(_inside(root, item) or _inside(item, root) for item in protected):
        raise ValidationError("Atlas export root is too broad or overlaps managed project data")
    if root.exists():
        if root.is_symlink() or not root.is_dir():
            raise ValidationError("Atlas export root is unavailable or unsafe")
        allowed_children = {".staging", "revisions", "current", "private-receipts"}
        for child in root.iterdir():
            if child.name not in allowed_children or child.is_symlink() or not child.is_dir():
                raise ConflictError("Atlas export root contains an unmanaged or unsafe entry")
    _assert_no_symlink_components(root)
    return root


class AtlasSnapshotExporter:
    """Publish one revision from copied source stores without source mutation."""

    def __init__(
        self,
        registry: object,
        source_stores: StoreMap,
        project_root: str | Path,
        export_root: str | Path,
        now: datetime | Callable[[], datetime],
        attempt_id_source: Callable[[], str],
        code_revision: str,
        failure_hook: Callable[[str], None] | None = None,
        *,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self.registry = registry
        self.source_stores = source_stores
        self.project_root = Path(project_root).resolve(strict=True)
        registry_project_root = Path(getattr(registry, "project_root", "")).resolve(strict=True)
        if self.project_root != registry_project_root:
            raise ValidationError("Atlas project root must match the validated registry project root")
        self.export_root = validate_export_root(export_root, registry)
        self.now = now
        self.attempt_id_source = attempt_id_source
        self.code_revision = code_revision
        self.failure_hook = failure_hook
        self.monotonic = time.monotonic if monotonic is None else monotonic
        if not self.project_root.is_dir() or self.project_root.is_symlink():
            raise ValidationError("Atlas project root is unavailable or unsafe")
        if not isinstance(code_revision, str) or not _CODE_REVISION.fullmatch(code_revision):
            raise ValidationError("Atlas code revision must be a safe bounded identifier")
        if not callable(attempt_id_source):
            raise ValidationError("Atlas attempt id source must be callable")
        if not callable(self.monotonic):
            raise ValidationError("Atlas monotonic clock must be callable")
        source_stores.validate_distinct()
        self._validate_source_separation()

    def _validate_source_separation(self) -> None:
        for _role, store in self.source_stores.items():
            if _inside(self.export_root, store) or _inside(store, self.export_root):
                raise ValidationError("Atlas export root cannot overlap a source store")

    def _query_bounds(self, declaration: object) -> dict[str, int]:
        query = _plain(getattr(declaration, "query_contract", {}))
        if not isinstance(query, dict) or not isinstance(query.get("bounds"), dict):
            raise ValidationError("Atlas export query bounds are unavailable")
        bounds = query["bounds"]
        if bounds != _QUERY_BOUNDS:
            raise ValidationError("Atlas export query bounds drifted")
        return dict(bounds)

    def _required_dataset_ids(self, declaration: object) -> dict[str, str]:
        query = _plain(getattr(declaration, "query_contract", {}))
        projections = query.get("projections") if isinstance(query, dict) else None
        if not isinstance(projections, list) or len(projections) != len(STORE_ROLES):
            raise ValidationError("Atlas required source datasets are incomplete")
        result: dict[str, str] = {}
        for role, projection in zip(STORE_ROLES, projections, strict=True):
            if not isinstance(projection, dict):
                raise ValidationError("Atlas required source dataset is invalid")
            dataset = projection.get("dataset")
            if projection.get("store") != role.value or not isinstance(dataset, str) or not dataset:
                raise ValidationError("Atlas required source dataset is invalid")
            result[role.value] = dataset
        if len(result) != len(STORE_ROLES):
            raise ValidationError("Atlas required source datasets are incomplete")
        return result

    def _monotonic_value(self) -> float:
        value = self.monotonic()
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValidationError("Atlas monotonic clock returned an invalid value")
        return float(value)

    def _deadline(self, declaration: object) -> float:
        return self._monotonic_value() + float(self._query_bounds(declaration)["max_runtime_seconds"])

    def _check_deadline(self, deadline: float, phase: str) -> None:
        if self._monotonic_value() > deadline:
            raise ResourceLimitError("Atlas export exceeded its registered runtime bound during " + phase)

    def _read_now(self) -> datetime:
        value = self.now() if callable(self.now) else self.now
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValidationError("Atlas export start must be an aware datetime")
        return value.astimezone(timezone.utc)

    def _cutoff(self) -> tuple[TemporalValue, str]:
        value = self._read_now()
        rendered = value.isoformat(timespec="microseconds").replace("+00:00", "Z")
        return TemporalValue.parse(rendered, pointer="/cutoff"), rendered

    def _attempt_id(self) -> str:
        value = self.attempt_id_source()
        if (
            not isinstance(value, str)
            or len(value) > 118
            or not _safe_file_name(value)
        ):
            raise ValidationError("Atlas attempt identifier is unsafe")
        return value

    def _hook(self, phase: str) -> None:
        if self.failure_hook is not None:
            self.failure_hook(phase)

    def _staging_root(self, attempt_id: str) -> Path:
        root = self.export_root / ".staging"
        _require_directory(self.export_root)
        _require_private_directory(root)
        attempt = root / attempt_id
        if attempt.exists() or attempt.is_symlink():
            raise ConflictError("Atlas staging attempt already exists")
        attempt.mkdir(mode=0o700)
        os.chmod(attempt, 0o700)
        _fsync_directory(root)
        return attempt

    def _cleanup_staging(self, attempt: Path) -> None:
        expected_parent = self.export_root / ".staging"
        if (
            attempt.parent != expected_parent
            or expected_parent.is_symlink()
            or not _safe_file_name(attempt.name)
        ):
            raise ValidationError("Atlas may clean up only its exact staging child")
        _assert_no_symlink_components(expected_parent)
        if attempt.exists():
            if attempt.is_symlink() or not attempt.is_dir():
                raise ValidationError("Atlas staging child is unsafe")
            shutil.rmtree(attempt)
            _fsync_directory(expected_parent)


    def _publication_lock_path(self) -> Path:
        _assert_no_symlink_components(self.export_root)
        created = False
        try:
            self.export_root.mkdir(parents=True, exist_ok=False, mode=0o755)
            created = True
        except FileExistsError:
            pass
        except OSError as exc:
            raise ValidationError("Atlas publication output root is unavailable") from exc
        if self.export_root.is_symlink() or not self.export_root.is_dir():
            raise ValidationError("Atlas publication output root is unsafe")
        if created:
            try:
                os.chmod(self.export_root, 0o755)
                _fsync_directory(self.export_root.parent)
            except OSError as exc:
                raise ValidationError("Atlas publication output root is unavailable") from exc
        private_root = self.export_root / "private-receipts"
        _require_exact_private_directory(private_root)
        locks_root = private_root / ".locks"
        _require_exact_private_directory(locks_root)
        path = locks_root / (_EXPORT_ID + ".lock")
        if not _safe_file_name(path.name):
            raise ValidationError("Atlas publication lock identifier is unsafe")
        return path

    def _open_publication_lock(self) -> int:
        path = self._publication_lock_path()
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            raise ValidationError("Atlas publication lock requires no-follow support")
        flags = os.O_RDWR | os.O_CREAT | nofollow | getattr(os, "O_CLOEXEC", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
        except OSError as exc:
            raise ValidationError("Atlas publication lock is unavailable") from exc
        try:
            descriptor_state = os.fstat(descriptor)
            path_state = os.lstat(path)
            if (
                not stat.S_ISREG(descriptor_state.st_mode)
                or not stat.S_ISREG(path_state.st_mode)
                or stat.S_IMODE(descriptor_state.st_mode) != 0o600
                or descriptor_state.st_dev != path_state.st_dev
                or descriptor_state.st_ino != path_state.st_ino
            ):
                raise ValidationError("Atlas publication lock is unsafe")
            _fsync_directory(path.parent)
            return descriptor
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise

    @contextmanager
    def _publication_lock(self, deadline: float):
        descriptor = self._open_publication_lock()
        acquired = False
        try:
            while True:
                self._check_deadline(deadline, "publication_lock_wait")
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    self._check_deadline(deadline, "publication_lock_acquired")
                    break
                except BlockingIOError:
                    remaining = deadline - self._monotonic_value()
                    if remaining <= 0:
                        raise ResourceLimitError(
                            "Atlas export exceeded its registered runtime bound during publication_lock_wait"
                        )
                    time.sleep(min(0.01, remaining))
                except OSError as exc:
                    raise ValidationError("Atlas publication lock acquisition failed") from exc
            yield
        finally:
            # Cleanup is deliberately non-raising so pointer commit remains final.
            if acquired:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError:
                    pass
            try:
                os.close(descriptor)
            except OSError:
                pass

    def _pointer_path(self) -> Path:
        return self.export_root / "current" / (_EXPORT_ID + ".json")

    def _read_pointer(self) -> bytes | None:
        pointer = self._pointer_path()
        if not pointer.exists():
            return None
        if pointer.is_symlink() or not pointer.is_file():
            raise ValidationError("Atlas current pointer is unavailable or unsafe")
        payload = pointer.read_bytes()
        parsed = loads_strict(payload)
        if not isinstance(parsed, dict) or set(parsed) != {
            "export_id", "manifest_sha256", "receipt_sha256", "revision_id"
        }:
            raise ValidationError("Atlas current pointer is malformed")
        if parsed.get("export_id") != _EXPORT_ID or not isinstance(parsed.get("revision_id"), str):
            raise ValidationError("Atlas current pointer is malformed")
        if not _REVISION_ID.fullmatch(parsed["revision_id"]):
            raise ValidationError("Atlas current pointer is malformed")
        if not isinstance(parsed.get("manifest_sha256"), str) or not _REVISION_ID.fullmatch(parsed["manifest_sha256"]):
            raise ValidationError("Atlas current pointer is malformed")
        if not isinstance(parsed.get("receipt_sha256"), str) or not _REVISION_ID.fullmatch(parsed["receipt_sha256"]):
            raise ValidationError("Atlas current pointer is malformed")
        return payload

    def _restore_pointer(self, prior: bytes | None) -> None:
        pointer = self._pointer_path()
        if prior is None:
            if pointer.exists():
                if pointer.is_symlink() or not pointer.is_file():
                    raise ValidationError("Atlas current pointer is unavailable or unsafe")
                pointer.unlink()
            return
        pointer.parent.mkdir(parents=True, exist_ok=True)
        _atomic_replace(pointer, prior)

    def _chunk_payloads(
        self,
        projection: AtlasProjection,
        max_rows: int,
        max_bytes: int,
        deadline: float | None = None,
    ) -> tuple[bytes, ...]:
        """Split deterministic ordered rows without an unbounded accumulation."""

        if max_rows < 1 or max_bytes < 1:
            raise ValidationError("Atlas chunk contract is invalid")
        result: list[bytes] = []
        current: list[Mapping[str, object]] = []
        for row in projection.rows:
            if deadline is not None:
                self._check_deadline(deadline, "chunk_encode")
            candidate = [*current, row]
            encoded = _strict_bytes(candidate)
            if len(encoded) > max_bytes and current:
                result.append(_strict_bytes(current))
                current = []
                encoded = _strict_bytes([row])
            if len(encoded) > max_bytes:
                raise ResourceLimitError("One Atlas public row exceeds the registered chunk byte bound")
            current.append(row)
            if len(current) == max_rows:
                result.append(_strict_bytes(current))
                current = []
        if current:
            result.append(_strict_bytes(current))
        return tuple(result)

    def _projection_contracts(self, declaration: object) -> dict[str, dict[str, object]]:
        query = _plain(getattr(declaration, "query_contract", {}))
        raw = query.get("projections") if isinstance(query, dict) else None
        if not isinstance(raw, list):
            raise ValidationError("Atlas projection contracts are unavailable")
        result: dict[str, dict[str, object]] = {}
        for item in raw:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise ValidationError("Atlas projection contract is invalid")
            if item["id"] in result:
                raise ValidationError("Atlas projection contract is duplicated")
            result[item["id"]] = item
        if set(result) != set(_LABELS):
            raise ValidationError("Atlas projection contracts are incomplete")
        return result

    def _schema_for(self, declaration: object, schema_id: str) -> dict[str, object]:
        contract = _plain(getattr(declaration, "schema_contract", {}))
        schemas = contract.get("schemas") if isinstance(contract, dict) else None
        if not isinstance(schemas, list):
            raise ValidationError("Atlas schema contract is unavailable")
        matches = [
            item for item in schemas
            if isinstance(item, dict) and item.get("id") == schema_id
        ]
        if len(matches) != 1:
            raise ValidationError("Atlas projection schema is not registered exactly once")
        return matches[0]

    def _public_key(self, row: Mapping[str, object], fields: tuple[str, ...]) -> list[object]:
        if not fields or any(field not in row for field in fields):
            raise ValidationError("Atlas public row identity is incomplete")
        return [row[field] for field in fields]

    def _write_chunks(
        self,
        payload_root: Path,
        projections: tuple[AtlasProjection, ...],
        declaration: object,
        deadline: float | None = None,
    ) -> tuple[dict[str, object], ...]:
        chunking = _plain(getattr(declaration, "chunking", {}))
        if not isinstance(chunking, dict):
            raise ValidationError("Atlas chunking contract is invalid")
        if (
            chunking.get("version") != "1.0.0"
            or chunking.get("strategy") != "ordered_rows"
            or chunking.get("empty_policy") != "omit"
        ):
            raise ValidationError("Atlas chunking contract drifted")
        max_rows = chunking.get("max_rows")
        max_bytes = chunking.get("max_bytes")
        if any(
            isinstance(item, bool) or not isinstance(item, int) or item < 1
            for item in (max_rows, max_bytes)
        ):
            raise ValidationError("Atlas chunk bounds are invalid")
        contracts = self._projection_contracts(declaration)
        records: list[dict[str, object]] = []
        for projection in projections:
            contract = contracts.get(projection.projection_id)
            if contract is None or not isinstance(contract.get("row_identity"), list):
                raise ValidationError("Atlas projection identity contract is invalid")
            identity = tuple(str(item) for item in contract["row_identity"])
            chunks: list[dict[str, object]] = []
            for index, payload in enumerate(
                self._chunk_payloads(
                    projection, max_rows, max_bytes, deadline=deadline
                ),
                start=1,
            ):
                if deadline is not None:
                    self._check_deadline(deadline, "chunk_write_before")
                relative = (
                    "data/chunks/" + projection.projection_id
                    + "-" + f"{index:04d}" + ".json"
                )
                _write_new(payload_root / relative, payload)
                if deadline is not None:
                    self._check_deadline(deadline, "chunk_write_after")
                parsed = loads_strict(payload)
                if not isinstance(parsed, list) or not parsed:
                    raise ValidationError("Atlas chunk was not a JSON row array")
                chunks.append(
                    {
                        "path": relative,
                        "sha256": _sha256(payload),
                        "rows": len(parsed),
                        "bytes": len(payload),
                        "first_key": self._public_key(parsed[0], identity),
                        "last_key": self._public_key(parsed[-1], identity),
                    }
                )
            dataset_rows = sum(int(item["rows"]) for item in chunks)
            dataset_bytes = sum(int(item["bytes"]) for item in chunks)
            records.append(
                {
                    "id": projection.projection_id,
                    "label": _LABELS[projection.projection_id],
                    "state": "ready",
                    "completeness": "complete",
                    "freshness": {
                        "state": "fixture",
                        "warnings": list(projection.warnings),
                    },
                    "schema": self._schema_for(declaration, projection.schema_id),
                    "rows": dataset_rows,
                    "bytes": dataset_bytes,
                    "key_range": {
                        "first_key": chunks[0]["first_key"] if chunks else [],
                        "last_key": chunks[-1]["last_key"] if chunks else [],
                    },
                    "chunks": chunks,
                }
            )
        return tuple(records)


    def _registry_primitive(self, declaration: object) -> dict[str, object]:
        query = _plain(getattr(declaration, "query_contract", {}))
        schema = _plain(getattr(declaration, "schema_contract", {}))
        if not isinstance(query, dict) or not isinstance(schema, dict):
            raise ValidationError("Atlas provenance contract is invalid")
        query_id, query_version = query.get("id"), query.get("version")
        schema_id, schema_version, schema_sha256 = (
            schema.get("id"),
            schema.get("version"),
            schema.get("sha256"),
        )
        if not all(isinstance(item, str) and item for item in (query_id, query_version, schema_id, schema_version)):
            raise ValidationError("Atlas provenance contract is invalid")
        if not isinstance(schema_sha256, str) or not _REVISION_ID.fullmatch(schema_sha256):
            raise ValidationError("Atlas provenance contract is invalid")
        return {
            "id": str(getattr(self.registry, "registry_id", "")),
            "revision": str(getattr(self.registry, "revision", "")),
            "schema_version": str(getattr(self.registry, "schema_version", "")),
            "code_revision": self.code_revision,
            "export": {
                "id": _EXPORT_ID,
                "version": str(getattr(declaration, "version", "")),
            },
            "query_contract": {"id": query_id, "version": query_version},
            "schema_contract": {
                "id": schema_id,
                "version": schema_version,
                "sha256": schema_sha256,
            },
        }

    def _manifest_registry(
        self,
        declaration: object,
        cohort: ReadOnlyCopyCohort,
    ) -> dict[str, object]:
        metadata = self._registry_primitive(declaration)
        metadata["source_cohort"] = {
            "coordination_started_at": cohort.coordination_started_at,
            "coordination_completed_at": cohort.coordination_completed_at,
            "cross_store_atomic": False,
        }
        return metadata

    def _public_sources(
        self,
        cohort: ReadOnlyCopyCohort,
        projections: tuple[AtlasProjection, ...],
    ) -> tuple[dict[str, object], ...]:
        selected = {projection.store: projection for projection in projections}
        records: list[dict[str, object]] = []
        for receipt in cohort.receipts:
            projection = selected.get(receipt.role)
            if projection is None:
                raise ValidationError("Atlas copy cohort is incomplete")
            records.append(
                {
                    "store": receipt.role,
                    "snapshot_at": receipt.completed_at,
                    "eligible_rows": len(projection.rows),
                    "eligible_rows_sha256": projection.row_sha256,
                }
            )
        return tuple(records)

    def _write_public_payload(
        self,
        payload_root: Path,
        declaration: object,
        cohort: ReadOnlyCopyCohort,
        projections: tuple[AtlasProjection, ...],
        cutoff: str,
        revision_id: str,
    ) -> dict[str, object]:
        datasets = self._write_chunks(payload_root, projections, declaration)
        schema = {
            "export_id": _EXPORT_ID,
            "version": str(getattr(declaration, "version", "")),
            "schemas": [item["schema"] for item in datasets],
        }
        _write_new(payload_root / "data" / "schema.json", _strict_bytes(schema))
        copy_site_bundle(self.project_root, payload_root)
        manifest = {
            "export_id": _EXPORT_ID,
            "revision_id": revision_id,
            "generated_at": cutoff,
            "cutoff": cutoff,
            "registry": self._manifest_registry(declaration, cohort),
            "source_stores": list(self._public_sources(cohort, projections)),
            "cross_store_atomic": False,
            "datasets": list(datasets),
        }
        _write_new(payload_root / "data" / "manifest.json", _strict_bytes(manifest))
        self._write_checksums(payload_root)
        return manifest


    def _write_checksums(self, payload_root: Path) -> None:
        entries: list[dict[str, object]] = []
        for path in sorted(payload_root.rglob("*")):
            if path.is_dir():
                if path.is_symlink():
                    raise ValidationError("Atlas public payload cannot contain symbolic links")
                continue
            if path.is_symlink() or not path.is_file():
                raise ValidationError("Atlas public payload contains an unsafe entry")
            relative = path.relative_to(payload_root).as_posix()
            if relative == "data/checksums.json":
                continue
            payload = path.read_bytes()
            entries.append(
                {
                    "path": relative,
                    "sha256": _sha256(payload),
                    "bytes": len(payload),
                }
            )
        _write_new(payload_root / "data" / "checksums.json", _strict_bytes({"files": entries}))

    def _assert_public_value(self, value: object) -> None:
        if isinstance(value, Mapping):
            forbidden = set(value).intersection(_FORBIDDEN_PUBLIC)
            if forbidden:
                raise ValidationError("Atlas public payload contains private fields")
            for item in value.values():
                self._assert_public_value(item)
        elif isinstance(value, list):
            for item in value:
                self._assert_public_value(item)

    def _safe_public_relative(self, raw: object) -> str:
        if not isinstance(raw, str) or not raw:
            raise ValidationError("Atlas public path is invalid")
        path = Path(raw)
        if path.is_absolute() or ".." in path.parts or path.as_posix() != raw or "\\" in raw:
            raise ValidationError("Atlas public path is invalid")
        if not (raw == "index.html" or raw.startswith("assets/") or raw.startswith("data/")):
            raise ValidationError("Atlas public path is invalid")
        return raw

    def _file_records(self, payload_root: Path) -> set[str]:
        result: set[str] = set()
        for path in payload_root.rglob("*"):
            if path.is_file():
                if path.is_symlink():
                    raise ValidationError("Atlas public payload cannot contain symbolic links")
                relative = path.relative_to(payload_root).as_posix()
                if relative != "data/checksums.json":
                    result.add(relative)
            elif path.is_symlink():
                raise ValidationError("Atlas public payload cannot contain symbolic links")
        return result


    def _validate_public_payload(
        self,
        payload_root: Path,
        manifest: Mapping[str, object],
        revision_id: str,
    ) -> str:
        if payload_root.is_symlink() or not payload_root.is_dir():
            raise ValidationError("Atlas public payload root is unavailable or unsafe")
        manifest_path = payload_root / "data" / "manifest.json"
        checksums_path = payload_root / "data" / "checksums.json"
        schema_path = payload_root / "data" / "schema.json"
        if not (manifest_path.is_file() and checksums_path.is_file() and schema_path.is_file()):
            raise ValidationError("Atlas public payload is incomplete")
        parsed_manifest = loads_strict(manifest_path.read_bytes())
        if parsed_manifest != manifest:
            raise ValidationError("Atlas public manifest did not round-trip exactly")
        required = {
            "export_id", "revision_id", "generated_at", "cutoff", "registry",
            "source_stores", "cross_store_atomic", "datasets",
        }
        if (
            not isinstance(parsed_manifest, dict)
            or set(parsed_manifest) != required
            or parsed_manifest.get("export_id") != _EXPORT_ID
            or parsed_manifest.get("revision_id") != revision_id
            or parsed_manifest.get("cross_store_atomic") is not False
        ):
            raise ValidationError("Atlas public manifest contract is invalid")
        if not isinstance(parsed_manifest.get("registry"), dict) or not isinstance(parsed_manifest.get("source_stores"), list):
            raise ValidationError("Atlas public manifest contract is invalid")
        self._assert_public_value(parsed_manifest)
        parsed_checksums = loads_strict(checksums_path.read_bytes())
        if not isinstance(parsed_checksums, dict) or set(parsed_checksums) != {"files"}:
            raise ValidationError("Atlas public checksums are invalid")
        records = parsed_checksums["files"]
        if not isinstance(records, list) or not records:
            raise ValidationError("Atlas public checksums are invalid")
        seen: set[str] = set()
        for record in records:
            if not isinstance(record, dict) or set(record) != {"path", "sha256", "bytes"}:
                raise ValidationError("Atlas public checksum record is invalid")
            relative = self._safe_public_relative(record["path"])
            if relative in seen or relative == "data/checksums.json":
                raise ValidationError("Atlas public checksum paths are invalid")
            seen.add(relative)
            digest = record["sha256"]
            byte_count = record["bytes"]
            if not isinstance(digest, str) or not _REVISION_ID.fullmatch(digest):
                raise ValidationError("Atlas public checksum digest is invalid")
            if isinstance(byte_count, bool) or not isinstance(byte_count, int) or byte_count < 0:
                raise ValidationError("Atlas public checksum byte count is invalid")
            target = payload_root / relative
            if not target.is_file() or target.is_symlink():
                raise ValidationError("Atlas public checksum target is unavailable")
            payload = target.read_bytes()
            if _sha256(payload) != digest or len(payload) != byte_count:
                raise ValidationError("Atlas public checksum verification failed")
        if seen != self._file_records(payload_root):
            raise ValidationError("Atlas public checksum inventory is incomplete")

        datasets = parsed_manifest["datasets"]
        if not isinstance(datasets, list) or len(datasets) != len(_LABELS):
            raise ValidationError("Atlas public datasets are incomplete")
        chunk_paths: set[str] = set()
        for dataset in datasets:
            expected = {
                "id", "label", "state", "completeness", "freshness", "schema", "chunks"
            }
            if not isinstance(dataset, dict) or set(dataset) != expected:
                raise ValidationError("Atlas public dataset contract is invalid")
            dataset_id = dataset.get("id")
            if dataset_id not in _LABELS or dataset.get("label") != _LABELS[dataset_id]:
                raise ValidationError("Atlas public dataset contract is invalid")
            if dataset.get("state") != "ready" or dataset.get("completeness") != "complete":
                raise ValidationError("Atlas public dataset state is invalid")
            if not isinstance(dataset.get("freshness"), dict) or dataset["freshness"] != {"state": "fixture"}:
                raise ValidationError("Atlas public freshness contract is invalid")
            if not isinstance(dataset.get("schema"), dict) or not isinstance(dataset.get("chunks"), list):
                raise ValidationError("Atlas public dataset schema is invalid")
            for chunk in dataset["chunks"]:
                if not isinstance(chunk, dict) or set(chunk) != {"path", "sha256", "rows", "bytes"}:
                    raise ValidationError("Atlas public chunk contract is invalid")
                relative = self._safe_public_relative(chunk["path"])
                if not relative.startswith("data/chunks/") or relative in chunk_paths:
                    raise ValidationError("Atlas public chunk path is invalid")
                chunk_paths.add(relative)
                target = payload_root / relative
                if not target.is_file() or target.is_symlink():
                    raise ValidationError("Atlas public chunk is unavailable")
                payload = target.read_bytes()
                if (
                    _sha256(payload) != chunk.get("sha256")
                    or len(payload) != chunk.get("bytes")
                    or not isinstance(chunk.get("rows"), int)
                    or isinstance(chunk.get("rows"), bool)
                ):
                    raise ValidationError("Atlas public chunk checksum verification failed")
                rows = loads_strict(payload)
                if not isinstance(rows, list) or len(rows) != chunk["rows"]:
                    raise ValidationError("Atlas public chunk row count is invalid")
                self._assert_public_value(rows)
        return _sha256(manifest_path.read_bytes())

    def _contract_primitive(self, declaration: object) -> dict[str, object]:
        query = _plain(getattr(declaration, "query_contract", {}))
        schema = _plain(getattr(declaration, "schema_contract", {}))
        chunking = _plain(getattr(declaration, "chunking", {}))
        consumers = _plain(getattr(declaration, "consumers", ()))
        if (
            not isinstance(query, dict)
            or not isinstance(schema, dict)
            or not isinstance(chunking, dict)
            or not isinstance(consumers, list)
            or not isinstance(schema.get("serialization"), dict)
        ):
            raise ValidationError("Atlas public contract metadata is invalid")
        return {
            "version": str(getattr(declaration, "version", "")),
            "query": {"id": query.get("id"), "version": query.get("version")},
            "schema": {
                "id": schema.get("id"),
                "version": schema.get("version"),
                "sha256": schema.get("sha256"),
            },
            "chunking": chunking,
            "consumers": consumers,
            "serialization": schema["serialization"],
        }

    def _receipt_datetime(self, value: object, *, pointer: str) -> str:
        if not isinstance(value, str):
            raise ValidationError("Atlas copy receipt timestamp is invalid")
        parsed = TemporalValue.parse(value, pointer=pointer)
        if parsed.precision.value != "datetime":
            raise ValidationError("Atlas copy receipt timestamp is invalid")
        return value

    def _migration_evidence(
        self,
        inspection: object,
        *,
        role: str,
    ) -> list[dict[str, object]]:
        if (
            getattr(inspection, "role", None) != role
            or getattr(inspection, "integrity", None) != "ok"
            or getattr(inspection, "foreign_key_violations", None) != 0
        ):
            raise ValidationError("Atlas copy inspection evidence is invalid")
        migrations = getattr(inspection, "migrations", None)
        if not isinstance(migrations, tuple) or not migrations:
            raise ValidationError("Atlas copy migration evidence is invalid")
        result: list[dict[str, object]] = []
        for migration in migrations:
            ordinal = getattr(migration, "ordinal", None)
            sha256 = getattr(migration, "sha256", None)
            reconstruction_state = getattr(migration, "reconstruction_state", None)
            if (
                isinstance(ordinal, bool)
                or not isinstance(ordinal, int)
                or ordinal < 1
                or not isinstance(sha256, str)
                or not _REVISION_ID.fullmatch(sha256)
                or not isinstance(reconstruction_state, str)
                or not reconstruction_state
            ):
                raise ValidationError("Atlas copy migration evidence is invalid")
            result.append(
                {
                    "ordinal": ordinal,
                    "sha256": sha256,
                    "reconstruction_state": reconstruction_state,
                }
            )
        return result

    def _source_copy_fingerprint(
        self,
        receipt: object,
        check: object,
        *,
        role: str,
    ) -> dict[str, object]:
        source_logical_sha256 = getattr(receipt, "source_logical_sha256", None)
        target_logical_sha256 = getattr(receipt, "target_logical_sha256", None)
        completed_at = self._receipt_datetime(
            getattr(receipt, "completed_at", None),
            pointer="/copy_receipt/completed_at",
        )
        if (
            getattr(receipt, "role", None) != role
            or not isinstance(source_logical_sha256, str)
            or not _REVISION_ID.fullmatch(source_logical_sha256)
            or not isinstance(target_logical_sha256, str)
            or not _REVISION_ID.fullmatch(target_logical_sha256)
            or source_logical_sha256 != target_logical_sha256
            or getattr(check, "role", None) != role
            or not isinstance(getattr(check, "dataset_id", None), str)
            or not getattr(check, "dataset_id")
            or not isinstance(getattr(check, "checkpoint_sha256", None), str)
            or not _REVISION_ID.fullmatch(getattr(check, "checkpoint_sha256"))
            or getattr(check, "complete", None) is not True
            or getattr(check, "running_runs", None) != 0
            or getattr(check, "unreconciled_failures", None) != 0
        ):
            raise ValidationError("Atlas source-copy receipt evidence is invalid")
        checkpoint_completed_at = self._receipt_datetime(
            getattr(check, "completed_at", None),
            pointer="/source_receipt_check/completed_at",
        )
        source_migrations = self._migration_evidence(
            getattr(receipt, "source_inspection", None), role=role
        )
        copy_migrations = self._migration_evidence(
            getattr(receipt, "target_inspection", None), role=role
        )
        if source_migrations != copy_migrations:
            raise ValidationError("Atlas copy migration evidence does not match source")
        return {
            "scope": "online_backup_copy",
            "copy_method": "online_backup",
            "source_logical_sha256": source_logical_sha256,
            "copy_logical_sha256": target_logical_sha256,
            "completed_at": completed_at,
            "migration_evidence": source_migrations,
            "checkpoint": {
                "dataset_id": check.dataset_id,
                "checkpoint_sha256": check.checkpoint_sha256,
                "completed_at": checkpoint_completed_at,
                "complete": True,
            },
        }

    def _provenance_primitive(
        self,
        declaration: object,
        cohort: ReadOnlyCopyCohort,
    ) -> dict[str, object]:
        query = _plain(getattr(declaration, "query_contract", {}))
        projections = query.get("projections") if isinstance(query, dict) else None
        excluded = query.get("excluded_fields") if isinstance(query, dict) else None
        if not isinstance(projections, list) or not isinstance(excluded, list):
            raise ValidationError("Atlas public provenance contract is invalid")
        coordination_started_at = self._receipt_datetime(
            cohort.coordination_started_at,
            pointer="/copy_cohort/coordination_started_at",
        )
        coordination_completed_at = self._receipt_datetime(
            cohort.coordination_completed_at,
            pointer="/copy_cohort/coordination_completed_at",
        )
        if cohort.cross_store_atomic is not False:
            raise ValidationError("Atlas copy cohort cannot claim cross-store atomicity")
        included: list[dict[str, object]] = []
        for projection in projections:
            if not isinstance(projection, dict):
                raise ValidationError("Atlas public provenance contract is invalid")
            included.append(
                {
                    "dataset_id": projection.get("dataset"),
                    "projection_id": projection.get("id"),
                    "fields": projection.get("fields"),
                }
            )
        return {
            "included_fields": included,
            "excluded_fields": excluded,
            "source_fingerprint_policy": {
                "source_mode": "online_backup",
                "consistent_copies": "online_backup_per_store",
                "completion_receipts": "required_complete",
                "public_scope": "online_backup_source_and_eligible_rows",
                "identifiers": [
                    "source_logical_sha256",
                    "copy_logical_sha256",
                    "migration_evidence",
                    "checkpoint_sha256",
                    "eligible_rows_sha256",
                    "schema_id",
                    "cutoff",
                ],
            },
            "coordination_window": {
                "started_at": coordination_started_at,
                "completed_at": coordination_completed_at,
            },
        }

    def _public_sources_v2(
        self,
        cohort: ReadOnlyCopyCohort,
        projections: tuple[AtlasProjection, ...],
        cutoff: str,
    ) -> list[dict[str, object]]:
        receipts = {item.role: item for item in cohort.receipts}
        checks = {item.role: item for item in cohort.source_receipt_checks}
        projected = {item.store: item for item in projections}
        if set(receipts) != set(checks) or set(receipts) != set(projected):
            raise ValidationError("Atlas source cohort evidence is incomplete")
        records: list[dict[str, object]] = []
        for role in (item.value for item in STORE_ROLES):
            receipt = receipts[role]
            check = checks[role]
            projection = projected[role]
            fingerprint = self._source_copy_fingerprint(
                receipt, check, role=role
            )
            records.append(
                {
                    "store": role,
                    "snapshot_at": fingerprint["completed_at"],
                    "source_logical_sha256": fingerprint[
                        "source_logical_sha256"
                    ],
                    "source_fingerprint": fingerprint,
                    "eligible_rows": len(projection.rows),
                    "eligible_rows_sha256": projection.row_sha256,
                }
            )
        return records

    def _source_identity_v2(
        self,
        projections: tuple[AtlasProjection, ...],
    ) -> list[dict[str, object]]:
        by_store = {item.store: item for item in projections}
        if set(by_store) != {item.value for item in STORE_ROLES}:
            raise ValidationError("Atlas eligible source projections are incomplete")
        records: list[dict[str, object]] = []
        for role in (item.value for item in STORE_ROLES):
            projection = by_store[role]
            if not _REVISION_ID.fullmatch(projection.row_sha256):
                raise ValidationError("Atlas eligible source projection digest is invalid")
            records.append(
                {
                    "store": role,
                    "schema_id": projection.schema_id,
                    "eligible_rows": len(projection.rows),
                    "eligible_rows_sha256": projection.row_sha256,
                }
            )
        return records

    def _checksum_records(self, payload_root: Path) -> list[dict[str, object]]:
        entries: list[dict[str, object]] = []
        for path in sorted(payload_root.rglob("*")):
            if path.is_dir():
                if path.is_symlink():
                    raise ValidationError("Atlas public payload cannot contain symbolic links")
                continue
            if path.is_symlink() or not path.is_file():
                raise ValidationError("Atlas public payload contains an unsafe entry")
            relative = path.relative_to(payload_root).as_posix()
            if relative == "data/checksums.json":
                continue
            payload = path.read_bytes()
            entries.append(
                {"path": relative, "sha256": _sha256(payload), "bytes": len(payload)}
            )
        return entries

    def _write_public_payload_v2(
        self,
        payload_root: Path,
        declaration: object,
        cohort: ReadOnlyCopyCohort,
        projections: tuple[AtlasProjection, ...],
        cutoff: str,
        revision_id: str,
        deadline: float,
    ) -> dict[str, object]:
        datasets = list(
            self._write_chunks(
                payload_root, projections, declaration, deadline=deadline
            )
        )
        schema_contract = _plain(getattr(declaration, "schema_contract", {}))
        if not isinstance(schema_contract, dict) or not isinstance(
            schema_contract.get("schemas"), list
        ):
            raise ValidationError("Atlas public schema contract is invalid")
        schema_payload = {
            "export_id": _EXPORT_ID,
            "version": str(getattr(declaration, "version", "")),
            "schemas": schema_contract["schemas"],
        }
        self._check_deadline(deadline, "schema_write_before")
        _write_new(payload_root / "data" / "schema.json", _strict_bytes(schema_payload))
        self._check_deadline(deadline, "site_bundle_before")
        copy_site_bundle(self.project_root, payload_root)
        self._check_deadline(deadline, "site_bundle_after")
        query = _plain(getattr(declaration, "query_contract", {}))
        lifecycle = _plain(getattr(declaration, "lifecycle", {}))
        if not isinstance(query, dict) or not isinstance(query.get("cutoff"), dict):
            raise ValidationError("Atlas point-in-time contract is invalid")
        if not isinstance(lifecycle, dict):
            raise ValidationError("Atlas lifecycle contract is invalid")
        total_rows = sum(int(item["rows"]) for item in datasets)
        chunk_bytes = sum(int(item["bytes"]) for item in datasets)
        manifest: dict[str, object] = {
            "export_id": _EXPORT_ID,
            "revision_id": revision_id,
            "generated_at": cutoff,
            "cutoff": cutoff,
            "semantic_dataset_id": str(
                getattr(declaration, "semantic_dataset_id", "")
            ),
            "lifecycle": lifecycle,
            "completeness": "complete",
            "point_in_time": {**query["cutoff"], "cutoff": cutoff},
            "registry": self._registry_primitive(declaration),
            "contract": self._contract_primitive(declaration),
            "provenance": self._provenance_primitive(declaration, cohort),
            "source_stores": self._public_sources_v2(
                cohort, projections, cutoff
            ),
            "cross_store_atomic": False,
            "totals": {
                "rows": total_rows,
                "chunk_bytes": chunk_bytes,
                "bytes": 0,
                "bounds": self._query_bounds(declaration),
            },
            "files": {
                "count": 0,
                "inventory_path": "data/checksums.json",
            },
            "datasets": datasets,
        }
        base_records = self._checksum_records(payload_root)
        manifest["files"]["count"] = len(base_records) + 2
        for _attempt in range(16):
            self._check_deadline(deadline, "manifest_fixed_point")
            manifest_payload = _strict_bytes(manifest)
            records = [
                *base_records,
                {
                    "path": "data/manifest.json",
                    "sha256": _sha256(manifest_payload),
                    "bytes": len(manifest_payload),
                },
            ]
            checksum_payload = _strict_bytes({"files": records})
            total_bytes = (
                sum(int(item["bytes"]) for item in base_records)
                + len(manifest_payload)
                + len(checksum_payload)
            )
            if total_bytes > self._query_bounds(declaration)["max_bytes"]:
                raise ResourceLimitError(
                    "Atlas public payload exceeds its registered byte bound"
                )
            if manifest["totals"]["bytes"] == total_bytes:
                break
            manifest["totals"]["bytes"] = total_bytes
        else:
            raise ValidationError("Atlas public manifest byte total did not converge")
        manifest_payload = _strict_bytes(manifest)
        records = [
            *base_records,
            {
                "path": "data/manifest.json",
                "sha256": _sha256(manifest_payload),
                "bytes": len(manifest_payload),
            },
        ]
        checksum_payload = _strict_bytes({"files": records})
        actual_total = (
            sum(int(item["bytes"]) for item in base_records)
            + len(manifest_payload)
            + len(checksum_payload)
        )
        if actual_total != manifest["totals"]["bytes"]:
            raise ValidationError("Atlas public manifest byte total is inconsistent")
        _write_new(payload_root / "data" / "manifest.json", manifest_payload)
        _write_new(payload_root / "data" / "checksums.json", checksum_payload)
        self._check_deadline(deadline, "public_files_complete")
        return manifest

    def _validate_typed_value(
        self,
        value: object,
        *,
        type_name: object,
        nullable: object,
    ) -> None:
        if not isinstance(nullable, bool) or not isinstance(type_name, str):
            raise ValidationError("Atlas public schema field contract is invalid")
        if value is None:
            if not nullable:
                raise ValidationError("Atlas public row violated a non-null field")
            return
        if isinstance(value, bool):
            raise ValidationError("Atlas public row contains a boolean type drift")
        if type_name == "integer":
            if type(value) is not int:
                raise ValidationError("Atlas public integer type drifted")
            return
        if type_name == "decimal_string":
            if not isinstance(value, str):
                raise ValidationError("Atlas public decimal type drifted")
            try:
                parsed = Decimal(value)
            except (InvalidOperation, ValueError) as exc:
                raise ValidationError("Atlas public decimal is invalid") from exc
            if not parsed.is_finite():
                raise ValidationError("Atlas public decimal is non-finite")
            return
        if not isinstance(value, str):
            raise ValidationError("Atlas public string type drifted")
        if type_name == "string":
            return
        if type_name in {"date", "datetime", "temporal_string"}:
            parsed_temporal = TemporalValue.parse(value, pointer="/public")
            if type_name == "date" and parsed_temporal.precision.value != "date":
                raise ValidationError("Atlas public date precision drifted")
            if type_name == "datetime" and parsed_temporal.precision.value != "datetime":
                raise ValidationError("Atlas public datetime precision drifted")
            return
        if type_name == "temporal_precision" and value in {"date", "datetime"}:
            return
        if type_name == "source_temporal_precision" and value in {
            "date", "datetime", "unknown"
        }:
            return
        raise ValidationError("Atlas public schema type is unsupported")

    def _order_atom(self, value: object) -> tuple[int, object]:
        if value is None:
            return (0, "")
        if type(value) is int:
            return (1, value)
        if isinstance(value, str):
            return (2, value)
        raise ValidationError("Atlas public order value has an unsupported type")

    def _validate_public_payload_v2(
        self,
        payload_root: Path,
        manifest: Mapping[str, object],
        revision_id: str,
        declaration: object,
        deadline: float | None = None,
    ) -> str:
        if deadline is not None:
            self._check_deadline(deadline, "validation_start")
        manifest_path = payload_root / "data" / "manifest.json"
        checksums_path = payload_root / "data" / "checksums.json"
        schema_path = payload_root / "data" / "schema.json"
        if any(
            path.is_symlink() or not path.is_file()
            for path in (manifest_path, checksums_path, schema_path)
        ):
            raise ValidationError("Atlas public payload is incomplete")
        parsed_manifest = loads_strict(manifest_path.read_bytes())
        if (
            not isinstance(parsed_manifest, dict)
            or set(parsed_manifest) != _PUBLIC_MANIFEST_KEYS
            or parsed_manifest != manifest
            or parsed_manifest.get("export_id") != _EXPORT_ID
            or parsed_manifest.get("revision_id") != revision_id
            or parsed_manifest.get("cross_store_atomic") is not False
        ):
            raise ValidationError("Atlas public manifest contract is invalid")
        self._assert_public_value(parsed_manifest)
        parsed_checksums = loads_strict(checksums_path.read_bytes())
        if not isinstance(parsed_checksums, dict) or set(parsed_checksums) != {"files"}:
            raise ValidationError("Atlas public checksums are invalid")
        records = parsed_checksums["files"]
        if not isinstance(records, list) or not records:
            raise ValidationError("Atlas public checksums are invalid")
        seen: set[str] = set()
        for record in records:
            if not isinstance(record, dict) or set(record) != {"path", "sha256", "bytes"}:
                raise ValidationError("Atlas public checksum record is invalid")
            relative = self._safe_public_relative(record["path"])
            if relative in seen or relative == "data/checksums.json":
                raise ValidationError("Atlas public checksum paths are invalid")
            seen.add(relative)
            target = payload_root / relative
            payload = target.read_bytes() if target.is_file() and not target.is_symlink() else b""
            if (
                not isinstance(record["sha256"], str)
                or not _REVISION_ID.fullmatch(record["sha256"])
                or isinstance(record["bytes"], bool)
                or not isinstance(record["bytes"], int)
                or record["bytes"] < 0
                or _sha256(payload) != record["sha256"]
                or len(payload) != record["bytes"]
            ):
                raise ValidationError("Atlas public checksum verification failed")
        if seen != self._file_records(payload_root):
            raise ValidationError("Atlas public checksum inventory is incomplete")
        all_files = [
            path for path in payload_root.rglob("*")
            if path.is_file() and not path.is_symlink()
        ]
        total_public_bytes = sum(path.stat().st_size for path in all_files)
        totals = parsed_manifest.get("totals")
        files = parsed_manifest.get("files")
        if (
            not isinstance(totals, dict)
            or set(totals) != {"rows", "chunk_bytes", "bytes", "bounds"}
            or totals.get("bounds") != self._query_bounds(declaration)
            or totals.get("bytes") != total_public_bytes
            or total_public_bytes > _QUERY_BOUNDS["max_bytes"]
            or not isinstance(files, dict)
            or files != {
                "count": len(all_files),
                "inventory_path": "data/checksums.json",
            }
        ):
            raise ValidationError("Atlas public aggregate inventory is invalid")
        datasets = parsed_manifest.get("datasets")
        contracts = self._projection_contracts(declaration)
        if (
            not isinstance(datasets, list)
            or tuple(item.get("id") for item in datasets if isinstance(item, dict))
            != tuple(_LABELS)
        ):
            raise ValidationError("Atlas public datasets are incomplete")
        total_rows = 0
        total_chunk_bytes = 0
        total_chunks = 0
        for dataset in datasets:
            if not isinstance(dataset, dict) or set(dataset) != {
                "id", "label", "state", "completeness", "freshness", "schema",
                "rows", "bytes", "key_range", "chunks",
            }:
                raise ValidationError("Atlas public dataset contract is invalid")
            contract = contracts[dataset["id"]]
            schema = self._schema_for(declaration, str(contract["schema_id"]))
            if (
                dataset["label"] != _LABELS[dataset["id"]]
                or dataset["state"] != "ready"
                or dataset["completeness"] != "complete"
                or dataset["schema"] != schema
                or not isinstance(dataset["chunks"], list)
                or len(dataset["chunks"]) > 20
            ):
                raise ValidationError("Atlas public dataset contract is invalid")
            field_contracts = schema.get("fields")
            identity = schema.get("row_identity")
            order_by = schema.get("order_by")
            if (
                not isinstance(field_contracts, list)
                or not isinstance(identity, list)
                or not isinstance(order_by, list)
            ):
                raise ValidationError("Atlas public schema is invalid")
            field_names = tuple(str(item.get("name")) for item in field_contracts)
            order_fields = tuple(str(item.get("field")) for item in order_by)
            identities: set[bytes] = set()
            previous_order: tuple[tuple[int, object], ...] | None = None
            dataset_rows = 0
            dataset_bytes = 0
            first_key: list[object] | None = None
            last_key: list[object] | None = None
            for chunk in dataset["chunks"]:
                if not isinstance(chunk, dict) or set(chunk) != {
                    "path", "sha256", "rows", "bytes", "first_key", "last_key"
                }:
                    raise ValidationError("Atlas public chunk contract is invalid")
                relative = self._safe_public_relative(chunk["path"])
                if not relative.startswith("data/chunks/"):
                    raise ValidationError("Atlas public chunk path is invalid")
                payload = (payload_root / relative).read_bytes()
                rows = loads_strict(payload)
                if (
                    not isinstance(rows, list)
                    or not rows
                    or len(rows) != chunk["rows"]
                    or len(rows) > 250
                    or len(payload) != chunk["bytes"]
                    or len(payload) > 262_144
                    or _sha256(payload) != chunk["sha256"]
                ):
                    raise ValidationError("Atlas public chunk bounds are invalid")
                for row in rows:
                    if not isinstance(row, dict) or set(row) != set(field_names):
                        raise ValidationError("Atlas public row fields are invalid")
                    for field in field_contracts:
                        self._validate_typed_value(
                            row[field["name"]],
                            type_name=field["type"],
                            nullable=field["nullable"],
                        )
                    identity_value = [row[str(field)] for field in identity]
                    identity_bytes = _strict_bytes(identity_value)
                    if identity_bytes in identities:
                        raise ValidationError("Atlas public row identity is duplicated")
                    identities.add(identity_bytes)
                    order_value = tuple(
                        self._order_atom(row[field]) for field in order_fields
                    )
                    if previous_order is not None and order_value <= previous_order:
                        raise ValidationError("Atlas public row order is not total")
                    previous_order = order_value
                    if first_key is None:
                        first_key = identity_value
                    last_key = identity_value
                if (
                    chunk["first_key"]
                    != [rows[0][str(field)] for field in identity]
                    or chunk["last_key"]
                    != [rows[-1][str(field)] for field in identity]
                ):
                    raise ValidationError("Atlas public chunk key range is invalid")
                dataset_rows += len(rows)
                dataset_bytes += len(payload)
                total_chunks += 1
            expected_range = {
                "first_key": [] if first_key is None else first_key,
                "last_key": [] if last_key is None else last_key,
            }
            max_rows = contract.get("bounds", {}).get("max_rows") if isinstance(contract.get("bounds"), dict) else None
            if (
                dataset["rows"] != dataset_rows
                or dataset["bytes"] != dataset_bytes
                or dataset["key_range"] != expected_range
                or isinstance(max_rows, bool)
                or not isinstance(max_rows, int)
                or dataset_rows > max_rows
            ):
                raise ValidationError("Atlas public dataset totals are invalid")
            total_rows += dataset_rows
            total_chunk_bytes += dataset_bytes
        if (
            total_rows != totals["rows"]
            or total_chunk_bytes != totals["chunk_bytes"]
            or total_rows > _QUERY_BOUNDS["max_total_rows"]
            or total_chunks > 48
        ):
            raise ValidationError("Atlas public totals are invalid")
        if deadline is not None:
            self._check_deadline(deadline, "validation_complete")
        return _sha256(manifest_path.read_bytes())

    def _revision_id(
        self,
        declaration: object,
        cutoff: str,
        projections: tuple[AtlasProjection, ...],
    ) -> str:
        material = {
            "export_id": _EXPORT_ID,
            "export_version": str(getattr(declaration, "version", "")),
            "registry": self._registry_primitive(declaration),
            "code_revision": self.code_revision,
            "cutoff": cutoff,
            "query_contract": _plain(getattr(declaration, "query_contract", {})),
            "schema_contract": _plain(getattr(declaration, "schema_contract", {})),
            "chunking": _plain(getattr(declaration, "chunking", {})),
            "source_mode": str(getattr(declaration, "source_mode", "")),
            "consistency": _plain(getattr(declaration, "consistency", {})),
            "eligible_rows": [
                {
                    "id": projection.projection_id,
                    "schema_id": projection.schema_id,
                    "rows": len(projection.rows),
                    "sha256": projection.row_sha256,
                }
                for projection in projections
            ],
        }
        return _sha256(_strict_bytes(material))


    def _revision_public_root(self, revision_id: str) -> Path:
        if not _REVISION_ID.fullmatch(revision_id):
            raise ValidationError("Atlas revision identifier is invalid")
        root = self.export_root / "revisions" / _EXPORT_ID
        _require_directory(self.export_root / "revisions")
        _require_directory(root)
        return root / revision_id / "public"

    def _validate_existing_revision(self, revision_id: str) -> str:
        payload_root = self._revision_public_root(revision_id)
        if payload_root.is_symlink() or not payload_root.is_dir():
            raise ValidationError("Atlas existing revision is unavailable or unsafe")
        manifest_path = payload_root / "data" / "manifest.json"
        if not manifest_path.is_file() or manifest_path.is_symlink():
            raise ValidationError("Atlas existing revision is incomplete")
        manifest = loads_strict(manifest_path.read_bytes())
        if not isinstance(manifest, dict):
            raise ValidationError("Atlas existing manifest is invalid")
        return self._validate_public_payload(payload_root, manifest, revision_id)

    def _promote_payload(
        self,
        attempt: Path,
        payload_root: Path,
        revision_id: str,
        manifest_sha256: str,
    ) -> tuple[bool, str]:
        final_public = self._revision_public_root(revision_id)
        final_revision = final_public.parent
        if final_revision.exists() or final_revision.is_symlink():
            if final_revision.is_symlink() or not final_revision.is_dir():
                raise ValidationError("Atlas immutable revision target is unsafe")
            return True, self._validate_existing_revision(revision_id)
        staged_revision = attempt / "revision"
        staged_revision.mkdir(mode=0o700)
        _make_public_tree(payload_root)
        os.replace(payload_root, staged_revision / "public")
        try:
            os.replace(staged_revision, final_revision)
        except FileExistsError:
            if final_revision.is_symlink() or not final_revision.is_dir():
                raise ValidationError("Atlas immutable revision target is unsafe")
            return True, self._validate_existing_revision(revision_id)
        except OSError as exc:
            raise ValidationError("Atlas immutable revision promotion failed") from exc
        try:
            os.chmod(final_revision, 0o755)
        except OSError as exc:
            raise ValidationError("Atlas immutable revision permission setup failed") from exc
        return False, self._validate_existing_revision(revision_id)

    def _private_receipt(
        self,
        cohort: ReadOnlyCopyCohort,
        attempt_id: str,
        cutoff: str,
        revision_id: str,
        manifest_sha256: str,
        reused: bool,
    ) -> dict[str, object]:
        primitive = {
            "export_id": _EXPORT_ID,
            "attempt_id": attempt_id,
            "cutoff": cutoff,
            "revision_id": revision_id,
            "manifest_sha256": manifest_sha256,
            "reused": reused,
            "copy_cohort": cohort.to_primitive(),
        }
        self._assert_private_receipt(primitive)
        return primitive

    def _assert_private_receipt(self, value: object) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if key in {"path", "store_map", "project_root", "export_root"}:
                    raise ValidationError("Atlas private receipt cannot disclose paths")
                self._assert_private_receipt(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                self._assert_private_receipt(item)

    def _write_private_receipt(self, attempt_id: str, receipt: Mapping[str, object]) -> str:
        root = self.export_root / "private-receipts" / _EXPORT_ID
        _require_private_directory(self.export_root / "private-receipts")
        _require_private_directory(root)
        if not _safe_file_name(attempt_id):
            raise ValidationError("Atlas private receipt identifier is unsafe")
        target = root / (attempt_id + ".json")
        payload = _strict_bytes(dict(receipt))
        _atomic_publish_new(target, payload, mode=0o600)
        return _sha256(payload)

    def _revision_id_v2(
        self,
        declaration: object,
        cutoff: str,
        projections: tuple[AtlasProjection, ...],
        cohort: ReadOnlyCopyCohort,
    ) -> str:
        material = {
            "export_id": _EXPORT_ID,
            "semantic_dataset_id": str(
                getattr(declaration, "semantic_dataset_id", "")
            ),
            "export_version": str(getattr(declaration, "version", "")),
            "registry": self._registry_primitive(declaration),
            "code_revision": self.code_revision,
            "cutoff": cutoff,
            "query_contract": _plain(getattr(declaration, "query_contract", {})),
            "schema_contract": _plain(getattr(declaration, "schema_contract", {})),
            "chunking": _plain(getattr(declaration, "chunking", {})),
            "source_mode": str(getattr(declaration, "source_mode", "")),
            "consistency": _plain(getattr(declaration, "consistency", {})),
            "source_fingerprints": self._source_identity_v2(projections),
            "eligible_rows": [
                {
                    "id": projection.projection_id,
                    "schema_id": projection.schema_id,
                    "rows": len(projection.rows),
                    "sha256": projection.row_sha256,
                }
                for projection in projections
            ],
        }
        return _sha256(_strict_bytes(material))

    def _validate_existing_revision_v2(
        self,
        revision_id: str,
        declaration: object,
    ) -> str:
        payload_root = self._revision_public_root(revision_id)
        if payload_root.is_symlink() or not payload_root.is_dir():
            raise ValidationError("Atlas existing revision is unavailable or unsafe")
        manifest_path = payload_root / "data" / "manifest.json"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise ValidationError("Atlas existing revision is incomplete")
        manifest = loads_strict(manifest_path.read_bytes())
        if not isinstance(manifest, dict):
            raise ValidationError("Atlas existing manifest is invalid")
        return self._validate_public_payload_v2(
            payload_root,
            manifest,
            revision_id,
            declaration,
        )

    def _promote_payload_v2(
        self,
        attempt: Path,
        payload_root: Path,
        revision_id: str,
        declaration: object,
    ) -> tuple[bool, str]:
        final_public = self._revision_public_root(revision_id)
        final_revision = final_public.parent
        if final_revision.exists() or final_revision.is_symlink():
            if final_revision.is_symlink() or not final_revision.is_dir():
                raise ValidationError("Atlas immutable revision target is unsafe")
            return True, self._validate_existing_revision_v2(
                revision_id, declaration
            )
        staged_revision = attempt / "revision"
        if staged_revision.exists() or staged_revision.is_symlink():
            raise ConflictError("Atlas staged revision already exists")
        staged_revision.mkdir(mode=0o700)
        _fsync_directory(attempt)
        _make_public_tree(payload_root)
        os.replace(payload_root, staged_revision / "public")
        _fsync_directory(staged_revision)
        try:
            os.replace(staged_revision, final_revision)
            _fsync_directory(final_revision.parent)
        except FileExistsError:
            if final_revision.is_symlink() or not final_revision.is_dir():
                raise ValidationError("Atlas immutable revision target is unsafe")
            return True, self._validate_existing_revision_v2(
                revision_id, declaration
            )
        except OSError as exc:
            raise ValidationError("Atlas immutable revision promotion failed") from exc
        try:
            os.chmod(final_revision, 0o755)
            _fsync_directory(final_revision.parent)
        except OSError as exc:
            raise ValidationError(
                "Atlas immutable revision permission setup failed"
            ) from exc
        return False, self._validate_existing_revision_v2(
            revision_id, declaration
        )

    def _prior_revision_id(self, pointer: bytes | None) -> str | None:
        if pointer is None:
            return None
        parsed = loads_strict(pointer)
        if not isinstance(parsed, dict) or not isinstance(
            parsed.get("revision_id"), str
        ):
            raise ValidationError("Atlas current pointer is malformed")
        return parsed["revision_id"]

    def _receipt_primitive(
        self,
        *,
        phase: str,
        cohort: ReadOnlyCopyCohort,
        projections: tuple[AtlasProjection, ...],
        declaration: object,
        attempt_id: str,
        cutoff: str,
        revision_id: str,
        manifest_sha256: str,
        prior_revision_id: str | None,
        reused: bool | None,
        precommit_receipt_sha256: str | None = None,
        cleanup_complete: bool,
    ) -> dict[str, object]:
        if phase not in {"precommit_intent", "publication"}:
            raise ValidationError("Atlas private receipt phase is invalid")
        row_records = [
            {
                "projection_id": item.projection_id,
                "read": len(item.rows),
                "accepted": len(item.rows),
                "rejected": 0,
                "written": len(item.rows),
            }
            for item in projections
        ]
        plan_material = {
            "export_id": _EXPORT_ID,
            "version": str(getattr(declaration, "version", "")),
            "cutoff": cutoff,
            "code_revision": self.code_revision,
        }
        if phase == "precommit_intent":
            if reused is not None:
                raise ValidationError("Atlas precommit receipt cannot report reuse")
            promotion_state = "validated"
            outcome = "validated"
        else:
            if not isinstance(reused, bool):
                raise ValidationError("Atlas publication receipt requires promotion state")
            promotion_state = "reused" if reused else "promoted"
            outcome = "revision_reused" if reused else "revision_promoted"
        primitive: dict[str, object] = {
            "receipt_version": "1.0.0",
            "phase": phase,
            "export_id": _EXPORT_ID,
            "attempt_id": attempt_id,
            "revision_id": revision_id,
            "manifest_sha256": manifest_sha256,
            "plan": {
                "sha256": _sha256(_strict_bytes(plan_material)),
                "export_version": str(getattr(declaration, "version", "")),
            },
            "trigger": "manual_fixture_only",
            "timing": {"started_at": cutoff, "completed_at": cutoff},
            "outcome": outcome,
            "copy_cohort": cohort.to_primitive(),
            "rows": row_records,
            "validation": {
                "state": "validated",
                "manifest_sha256": manifest_sha256,
                "bounds": self._query_bounds(declaration),
            },
            "benchmark_policy": _plain(
                getattr(declaration, "benchmark", {})
            ),
            "promotion": {
                "state": promotion_state,
                "reused": reused,
            },
            "targets": {
                "staging": "staging-attempt",
                "revision": "immutable-revision",
                "current": "current-pointer",
            },
            "prior_current_revision_id": prior_revision_id,
            "new_current_revision_id": revision_id,
            "pointer_state": "not_published",
            "pointer_intent": "current",
            "warnings": sorted(
                {warning for item in projections for warning in item.warnings}
            ),
            "errors": [],
            "cleanup": {
                "exact_child_only": True,
                "staging_removed": cleanup_complete,
                "reconciliation": (
                    "intent_durable"
                    if phase == "precommit_intent"
                    else "publication_receipt_durable"
                ),
            },
        }
        if precommit_receipt_sha256 is not None:
            primitive["precommit_receipt_sha256"] = precommit_receipt_sha256
        self._assert_private_receipt(primitive)
        return primitive


    def publish(self, export_id: str = _EXPORT_ID) -> AtlasPublication:
        """Create or reuse one validated immutable offline Atlas revision."""

        if export_id != _EXPORT_ID:
            raise ValidationError("Only atlas.fixture_snapshot may be published")
        declaration = _declaration(self.registry, export_id)
        deadline = self._deadline(declaration)
        with self._publication_lock(deadline):
            return self._publish_locked(export_id, declaration, deadline)

    def _publish_locked(
        self,
        export_id: str,
        declaration: object,
        deadline: float,
    ) -> AtlasPublication:
        cutoff, cutoff_text = self._cutoff()
        self._check_deadline(deadline, "export_start")
        attempt_id = self._attempt_id()
        previous_pointer = self._read_pointer()
        prior_revision_id = self._prior_revision_id(previous_pointer)
        self._hook("after_pointer_read")
        attempt = self._staging_root(attempt_id)
        try:
            source_copy_root = attempt / "source-copies"
            _require_private_directory(source_copy_root)
            cohort = capture_readonly_copies(
                self.source_stores,
                self.registry,
                source_copy_root,
                self.now,
                required_dataset_ids_by_role=self._required_dataset_ids(declaration),
                progress_check=lambda phase: self._check_deadline(deadline, phase),
            )
            _make_private_tree(source_copy_root)
            self._hook("after_copy")
            projections = collect_atlas_projections(
                self.registry,
                cohort.copy_store_map,
                declaration,
                cutoff,
                deadline=deadline,
                monotonic=self.monotonic,
            )
            self._hook("after_query")
            revision_id = self._revision_id_v2(
                declaration,
                cutoff_text,
                projections,
                cohort,
            )
            payload_root = attempt / "public-payload"
            payload_root.mkdir(mode=0o755)
            _fsync_directory(attempt)
            manifest = self._write_public_payload_v2(
                payload_root,
                declaration,
                cohort,
                projections,
                cutoff_text,
                revision_id,
                deadline,
            )
            self._hook("after_files")
            manifest_sha256 = self._validate_public_payload_v2(
                payload_root,
                manifest,
                revision_id,
                declaration,
                deadline=deadline,
            )
            self._hook("after_validation")
            self._check_deadline(deadline, "precommit_receipt")
            precommit_receipt_sha256 = self._write_private_receipt(
                attempt_id + ".precommit",
                self._receipt_primitive(
                    phase="precommit_intent",
                    cohort=cohort,
                    projections=projections,
                    declaration=declaration,
                    attempt_id=attempt_id,
                    cutoff=cutoff_text,
                    revision_id=revision_id,
                    manifest_sha256=manifest_sha256,
                    prior_revision_id=prior_revision_id,
                    reused=None,
                    cleanup_complete=False,
                ),
            )
            self._hook("after_precommit_intent")
            self._check_deadline(deadline, "revision_promotion")
            reused, promoted_sha256 = self._promote_payload_v2(
                attempt,
                payload_root,
                revision_id,
                declaration,
            )
            if not reused and promoted_sha256 != manifest_sha256:
                raise ValidationError("Atlas immutable revision digest did not match staged payload")
            manifest_sha256 = promoted_sha256
            self._hook("after_revision_promotion")
            self._cleanup_staging(attempt)
            self._check_deadline(deadline, "publication_receipt")
            receipt_sha256 = self._write_private_receipt(
                attempt_id,
                self._receipt_primitive(
                    phase="publication",
                    cohort=cohort,
                    projections=projections,
                    declaration=declaration,
                    attempt_id=attempt_id,
                    cutoff=cutoff_text,
                    revision_id=revision_id,
                    manifest_sha256=manifest_sha256,
                    prior_revision_id=prior_revision_id,
                    reused=reused,
                    precommit_receipt_sha256=precommit_receipt_sha256,
                    cleanup_complete=True,
                ),
            )
            self._hook("after_publication_receipt")
            pointer = self._pointer_path()
            _require_directory(pointer.parent)
            pointer_payload = _strict_bytes(
                {
                    "export_id": _EXPORT_ID,
                    "revision_id": revision_id,
                    "manifest_sha256": manifest_sha256,
                    "receipt_sha256": receipt_sha256,
                }
            )
            self._hook("before_pointer")
        except BaseException:
            self._cleanup_staging(attempt)
            raise

        # This atomic replacement is the commit point. No fallible work,
        # hook, cleanup, or rollback follows it.
        _atomic_replace(pointer, pointer_payload)
        return AtlasPublication(
            export_id=_EXPORT_ID,
            revision_id=revision_id,
            cutoff=cutoff_text,
            manifest_sha256=manifest_sha256,
            receipt_sha256=receipt_sha256,
            reused=reused,
            current_revision_id=revision_id,
        )


__all__ = ("AtlasSnapshotExporter", "validate_export_root")
