"""One-time local adoption of the retained Stage 11 EIA weekly cohort.

This operation deliberately has no transport, credential, scheduler, or
migration path.  It transfers the already-published Stage 11 weekly evidence
bundle row by row from the retained private candidate into the fixed canonical
macro store.  The original run, capture, artifact, snapshot, quality, version,
and current-pointer identities are preserved verbatim, so the canonical store
keeps the provider evidence lineage rather than inventing an adoption vintage.

The runner accepts explicit temporary paths for offline tests.  The public
zero-argument entry point is intentionally fixed to the reviewed retained
candidate and ``data/macro.sqlite``; callers cannot provide paths, SQL, or a
network endpoint.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import sqlite3
import stat
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
from urllib.parse import quote

from ..errors import ConflictError, ResourceLimitError, StoreUnavailableError, ValidationError
from ..json_codec import dumps_strict, loads_strict
from ..macro.stage11_publication import (
    EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_DATASET_ID,
    EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_EVIDENCE_DATASET_ID,
    STAGE11_MIGRATION_ID,
    STAGE11_MIGRATION_RESOURCE,
    STAGE11_MIGRATION_SHA256,
)
from ..registry import CANONICAL_REGISTRY_PATH, load_registry
from ..stores import StoreMap, StoreRole, StoreWriteLock, writer_connection


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_MACRO_STORE = PROJECT_ROOT / "data" / "macro.sqlite"
RETAINED_STAGE11_WEEKLY_SOURCE = Path(
    "/home/volatility/quant-data-nonprod/"
    "stage10-fmp-market-history-v1/stores/macro.sqlite"
)

_CANONICAL_SERIES_ID = "macro.eia.weekly.petroleum_stock"
_PROVIDER_SERIES_ID = "PET.WCESTUS1.W"
_EVIDENCE_DATASET_ID = EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_EVIDENCE_DATASET_ID
_CANONICAL_DATASET_ID = EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_DATASET_ID
_OUTPUT_DATASET_IDS = (_EVIDENCE_DATASET_ID, _CANONICAL_DATASET_ID)
_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_SCOPE_BYTES = 256 * 1024
_MAX_BUNDLE_BYTES = 8 * 1024 * 1024
_SOURCE_SIDECARS = ("-wal", "-shm", "-journal")
_BUNDLE_CONTRACT = "quant_data.stage11_weekly_retained_bundle"
_BUNDLE_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class Stage11WeeklySourceExpectation:
    """Sealed source shape accepted by one adoption invocation."""

    current_count: int
    version_count: int
    first_period: str
    last_period: str
    response_sha256: str
    bundle_sha256: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.current_count, bool)
            or not isinstance(self.current_count, int)
            or self.current_count < 1
            or isinstance(self.version_count, bool)
            or not isinstance(self.version_count, int)
            or self.version_count < self.current_count
            or not isinstance(self.first_period, str)
            or not isinstance(self.last_period, str)
            or _DATE.fullmatch(self.first_period) is None
            or _DATE.fullmatch(self.last_period) is None
            or self.first_period > self.last_period
            or not isinstance(self.response_sha256, str)
            or _SHA256.fullmatch(self.response_sha256) is None
            or not isinstance(self.bundle_sha256, str)
            or _SHA256.fullmatch(self.bundle_sha256) is None
        ):
            raise ValidationError("Stage 11 weekly source expectation is invalid")


_CANONICAL_SOURCE_EXPECTATION = Stage11WeeklySourceExpectation(
    current_count=2_289,
    version_count=2_289,
    first_period="1982-08-20",
    last_period="2026-08-07",
    response_sha256="f1efed3cf1e3570ba1439c7adfc0db6c9a4e21c7b56e26190ffc0b3c56d4e737",
    bundle_sha256="d99195d5b9b2f9ded9243d383677a63c61f77952edfe07b740e7a3fc4d701f8f",
)


@dataclass(frozen=True, slots=True)
class Stage11WeeklyAdoptionReport:
    """Sanitized result of the one-time local adoption."""

    outcome: str
    source_current_count: int
    source_version_count: int
    target_current_count: int
    target_version_count: int
    inserted_capture_count: int
    inserted_version_count: int
    inserted_current_count: int

    def __post_init__(self) -> None:
        if self.outcome not in {"adopted", "unchanged"}:
            raise ValidationError("Stage 11 weekly adoption outcome is invalid")
        values = (
            self.source_current_count,
            self.source_version_count,
            self.target_current_count,
            self.target_version_count,
            self.inserted_capture_count,
            self.inserted_version_count,
            self.inserted_current_count,
        )
        if any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in values):
            raise ValidationError("Stage 11 weekly adoption counts are invalid")
        if (
            self.source_current_count != self.target_current_count
            or self.source_version_count != self.target_version_count
        ):
            raise ValidationError("Stage 11 weekly adoption target counts are invalid")
        if self.outcome == "adopted" and (
            self.inserted_capture_count != 1
            or self.inserted_version_count != self.source_version_count
            or self.inserted_current_count != self.source_current_count
        ):
            raise ValidationError("Stage 11 weekly adoption write counts are invalid")
        if self.outcome == "unchanged" and any(
            item != 0
            for item in (
                self.inserted_capture_count,
                self.inserted_version_count,
                self.inserted_current_count,
            )
        ):
            raise ValidationError("Stage 11 weekly replay must be a no-write")

    def mapping(self) -> dict[str, object]:
        return {
            "contract": "quant_data.stage11_weekly_adoption",
            "current_count": self.target_current_count,
            "inserted": {
                "captures": self.inserted_capture_count,
                "current": self.inserted_current_count,
                "versions": self.inserted_version_count,
            },
            "outcome": self.outcome,
            "version_count": self.target_version_count,
        }


@dataclass(frozen=True, slots=True)
class _Rows:
    columns: tuple[str, ...]
    values: tuple[tuple[object, ...], ...]

    def one(self, label: str) -> tuple[object, ...]:
        if len(self.values) != 1:
            raise ConflictError(f"Stage 11 weekly {label} is incomplete or ambiguous")
        return self.values[0]

    def field(self, row: tuple[object, ...], name: str) -> object:
        return row[self.columns.index(name)]


@dataclass(frozen=True, slots=True)
class _Bundle:
    capture: _Rows
    run: _Rows
    outputs: _Rows
    artifacts: _Rows
    snapshot: _Rows
    memberships: _Rows
    quality: _Rows
    versions: _Rows
    current: _Rows
    checkpoints: _Rows

    @property
    def run_id(self) -> str:
        value = self.run.field(self.run.one("run"), "run_id")
        if not isinstance(value, str) or not value:
            raise ConflictError("Stage 11 weekly run identity is invalid")
        return value

    @property
    def semantic_identity(self) -> str:
        value = self.run.field(self.run.one("run"), "semantic_identity")
        if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
            raise ConflictError("Stage 11 weekly semantic identity is invalid")
        return value


@dataclass(frozen=True, slots=True)
class _FileStamp:
    device: int
    inode: int
    mode: int
    links: int
    size: int
    mtime_ns: int
    ctime_ns: int


_CAPTURE_COLUMNS = (
    "capture_id",
    "dataset_id",
    "provider",
    "endpoint_path",
    "provider_series_id",
    "request_scope_json",
    "request_scope_sha256",
    "response_sha256",
    "response_bytes",
    "semantic_identity",
    "completeness",
    "availability_basis",
    "artifact_id",
    "snapshot_id",
    "captured_at",
    "captured_precision",
    "row_count",
    "normalization_version",
    "run_id",
)
_RUN_COLUMNS = (
    "run_id",
    "dataset_id",
    "semantic_identity",
    "command",
    "scope_json",
    "status",
    "started_at",
    "completed_at",
    "artifact_id",
    "snapshot_id",
    "fetched_count",
    "written_count",
    "warnings_json",
    "code_version",
)
_OUTPUT_COLUMNS = ("run_id", "dataset_id", "semantic_identity")
_ARTIFACT_COLUMNS = (
    "artifact_id",
    "run_id",
    "dataset_id",
    "content_sha256",
    "media_type",
    "byte_count",
    "source_reference",
    "request_scope_json",
    "captured_at",
    "captured_precision",
    "normalization_version",
)
_SNAPSHOT_COLUMNS = (
    "snapshot_id",
    "run_id",
    "dataset_id",
    "semantic_identity",
    "scope_json",
    "completeness",
    "row_count",
    "captured_at",
    "captured_precision",
    "validation_state",
    "warnings_json",
)
_MEMBERSHIP_COLUMNS = ("snapshot_id", "artifact_id", "artifact_ordinal")
_QUALITY_COLUMNS = (
    "quality_result_id",
    "run_id",
    "dataset_id",
    "rule_id",
    "rule_version",
    "severity",
    "outcome",
    "subject_kind",
    "subject_id",
    "artifact_id",
    "snapshot_id",
    "observed_json",
    "created_at",
)
_VERSION_COLUMNS = (
    "version_id",
    "canonical_series_id",
    "provider_series_id",
    "period",
    "value_text",
    "unit",
    "content_sha256",
    "available_at",
    "available_precision",
    "captured_at",
    "captured_precision",
    "correction_sequence",
    "supersedes_version_id",
    "capture_id",
    "artifact_id",
    "snapshot_id",
    "run_id",
    "source_row",
)
_CURRENT_COLUMNS = ("canonical_series_id", "period", "current_version_id")
_CHECKPOINT_COLUMNS = (
    "dataset_id",
    "store_role",
    "layer",
    "schema_version",
    "relations_json",
    "active",
    "last_successful_run_id",
    "last_semantic_identity",
)


def _typed_sqlite_value(value: object) -> dict[str, object]:
    """Render one SQLite value without collapsing text, integer, or blob types."""

    if value is None:
        return {"type": "null"}
    if isinstance(value, bytes):
        return {"hex": value.hex(), "type": "blob"}
    if isinstance(value, str):
        return {"type": "text", "value": value}
    if isinstance(value, int) and not isinstance(value, bool):
        return {"type": "integer", "value": value}
    raise ConflictError("Stage 11 weekly retained bundle has an unsupported SQLite value type")


def _rows_material(name: str, rows: _Rows) -> dict[str, object]:
    return {
        "columns": list(rows.columns),
        "name": name,
        "rows": [
            [_typed_sqlite_value(value) for value in row]
            for row in rows.values
        ],
    }


def _bundle_material(bundle: _Bundle) -> dict[str, object]:
    """Exact type-preserving material for the retained weekly evidence bundle."""

    return {
        "contract": _BUNDLE_CONTRACT,
        "contract_version": _BUNDLE_VERSION,
        "tables": [
            _rows_material("stage11_eia_weekly_captures", bundle.capture),
            _rows_material("ingestion_runs", bundle.run),
            _rows_material("ingestion_run_outputs", bundle.outputs),
            _rows_material("ingestion_artifacts", bundle.artifacts),
            _rows_material("ingestion_snapshots", bundle.snapshot),
            _rows_material("ingestion_snapshot_artifacts", bundle.memberships),
            _rows_material("data_quality_results", bundle.quality),
            _rows_material("stage11_eia_weekly_observation_versions", bundle.versions),
            _rows_material("stage11_eia_weekly_observations", bundle.current),
            _rows_material("dataset_registry_checkpoints", bundle.checkpoints),
        ],
    }


def _bundle_sha256(bundle: _Bundle) -> str:
    try:
        rendered = dumps_strict(_bundle_material(bundle), max_bytes=_MAX_BUNDLE_BYTES)
    except (ResourceLimitError, ValidationError) as exc:
        raise ConflictError("Stage 11 weekly retained bundle cannot be serialized") from exc
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _require_timeout(value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or value < 0
    ):
        raise ValidationError("Stage 11 weekly lock timeout is invalid")
    return float(value)


def _resolve_regular(path: str | Path, label: str) -> Path:
    supplied = Path(path)
    try:
        info = supplied.lstat()
        resolved = supplied.resolve(strict=True)
        resolved_info = resolved.stat()
    except (OSError, RuntimeError, ValueError) as exc:
        raise StoreUnavailableError(f"Stage 11 weekly {label} is unavailable") from exc
    if (
        supplied.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or not stat.S_ISREG(resolved_info.st_mode)
        or resolved_info.st_nlink != 1
    ):
        raise ValidationError(f"Stage 11 weekly {label} binding is invalid")
    return resolved


def _stamp(path: Path) -> _FileStamp:
    try:
        info = path.stat()
    except OSError as exc:
        raise StoreUnavailableError("Stage 11 weekly retained source is unavailable") from exc
    return _FileStamp(
        device=info.st_dev,
        inode=info.st_ino,
        mode=stat.S_IMODE(info.st_mode),
        links=info.st_nlink,
        size=info.st_size,
        mtime_ns=info.st_mtime_ns,
        ctime_ns=info.st_ctime_ns,
    )


def _source_sidecar_stamps(path: Path) -> tuple[_FileStamp | None, ...]:
    """Return immutable source sidecars while rejecting a stale-WAL hazard.

    SQLite may leave a harmless ``-shm`` file behind after an ordinary read.
    It is not used by the immutable URI below, but its identity must remain
    unchanged.  A WAL or rollback journal could contain facts absent from the
    main file, so it is a fail-closed condition for this sealed transfer.
    """

    result: list[_FileStamp | None] = []
    for suffix in _SOURCE_SIDECARS:
        sidecar = path.with_name(path.name + suffix)
        try:
            info = sidecar.lstat()
        except FileNotFoundError:
            result.append(None)
            continue
        except OSError as exc:
            raise StoreUnavailableError("Stage 11 weekly retained source is unavailable") from exc
        if not stat.S_ISREG(info.st_mode) or sidecar.is_symlink():
            raise ValidationError("Stage 11 weekly retained source sidecar binding is invalid")
        stamp = _FileStamp(
            device=info.st_dev,
            inode=info.st_ino,
            mode=stat.S_IMODE(info.st_mode),
            links=info.st_nlink,
            size=info.st_size,
            mtime_ns=info.st_mtime_ns,
            ctime_ns=info.st_ctime_ns,
        )
        if suffix in {"-wal", "-journal"}:
            raise ConflictError("Stage 11 weekly retained source has unsealed SQLite state")
        result.append(stamp)
    return tuple(result)


def _immutable_uri(path: Path) -> str:
    return "file:" + quote(path.as_posix(), safe="/") + "?mode=ro&immutable=1"


@contextmanager
def _source_connection(path: Path) -> Iterator[sqlite3.Connection]:
    """Read the retained source through an immutable, non-writing connection."""

    before = _stamp(path)
    before_sidecars = _source_sidecar_stamps(path)
    try:
        connection = sqlite3.connect(_immutable_uri(path), uri=True, isolation_level=None)
    except sqlite3.Error as exc:
        raise StoreUnavailableError("Stage 11 weekly retained source is unavailable") from exc
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN")
        yield connection
    except sqlite3.Error as exc:
        raise ConflictError("Stage 11 weekly retained source is unreadable") from exc
    finally:
        if connection.in_transaction:
            connection.rollback()
        connection.close()
        if _stamp(path) != before or _source_sidecar_stamps(path) != before_sidecars:
            raise ConflictError("Stage 11 weekly retained source changed during adoption")


def _require_integrity(connection: sqlite3.Connection, label: str) -> None:
    integrity = tuple(str(row[0]) for row in connection.execute("PRAGMA integrity_check"))
    if integrity != ("ok",):
        raise ConflictError(f"Stage 11 weekly {label} integrity check failed")
    if tuple(connection.execute("PRAGMA foreign_key_check")):
        raise ConflictError(f"Stage 11 weekly {label} has foreign-key violations")


def _require_macro_store(connection: sqlite3.Connection, label: str) -> None:
    row = connection.execute(
        "SELECT store_role FROM store_metadata WHERE singleton=1"
    ).fetchone()
    if row is None or row[0] != StoreRole.MACRO.value:
        raise ConflictError(f"Stage 11 weekly {label} is not a macro store")
    migration = connection.execute(
        """
        SELECT store_role, ordinal, resource, sha256
        FROM schema_migrations
        WHERE migration_id=?
        """,
        (STAGE11_MIGRATION_ID,),
    ).fetchone()
    if migration is None or tuple(migration) != (
        StoreRole.MACRO.value,
        12,
        STAGE11_MIGRATION_RESOURCE,
        STAGE11_MIGRATION_SHA256,
    ):
        raise ConflictError(f"Stage 11 weekly {label} lacks the reviewed Stage 11 schema")


def _rows(
    connection: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
    *,
    where: str = "",
    parameters: tuple[object, ...] = (),
    order_by: str,
) -> _Rows:
    # All identifiers originate in this module's fixed schema map.
    sql = f"SELECT {', '.join(columns)} FROM {table}"
    if where:
        sql += " WHERE " + where
    sql += " ORDER BY " + order_by
    fetched = connection.execute(sql, parameters).fetchall()
    return _Rows(columns=columns, values=tuple(tuple(row[column] for column in columns) for row in fetched))


def _json_object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, str):
        raise ConflictError(f"Stage 11 weekly {label} is invalid")
    try:
        parsed = loads_strict(value, max_bytes=_MAX_SCOPE_BYTES)
    except (ValidationError, ValueError):
        raise ConflictError(f"Stage 11 weekly {label} is invalid") from None
    if not isinstance(parsed, dict) or dumps_strict(parsed, max_bytes=_MAX_SCOPE_BYTES) != value:
        raise ConflictError(f"Stage 11 weekly {label} is invalid")
    return parsed


def _read_source_bundle(connection: sqlite3.Connection) -> _Bundle:
    _require_macro_store(connection, "retained source")
    _require_integrity(connection, "retained source")
    capture = _rows(
        connection,
        "stage11_eia_weekly_captures",
        _CAPTURE_COLUMNS,
        order_by="capture_id",
    )
    capture_row = capture.one("capture")
    run_id = capture.field(capture_row, "run_id")
    if not isinstance(run_id, str) or not run_id:
        raise ConflictError("Stage 11 weekly retained capture has no run identity")
    run = _rows(
        connection,
        "ingestion_runs",
        _RUN_COLUMNS,
        where="run_id=?",
        parameters=(run_id,),
        order_by="run_id",
    )
    run_row = run.one("ingestion run")
    snapshot_id = run.field(run_row, "snapshot_id")
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise ConflictError("Stage 11 weekly retained run has no snapshot identity")
    outputs = _rows(
        connection,
        "ingestion_run_outputs",
        _OUTPUT_COLUMNS,
        where="run_id=?",
        parameters=(run_id,),
        order_by="dataset_id",
    )
    artifacts = _rows(
        connection,
        "ingestion_artifacts",
        _ARTIFACT_COLUMNS,
        where="run_id=?",
        parameters=(run_id,),
        order_by="artifact_id",
    )
    snapshot = _rows(
        connection,
        "ingestion_snapshots",
        _SNAPSHOT_COLUMNS,
        where="run_id=?",
        parameters=(run_id,),
        order_by="snapshot_id",
    )
    memberships = _rows(
        connection,
        "ingestion_snapshot_artifacts",
        _MEMBERSHIP_COLUMNS,
        where="snapshot_id=?",
        parameters=(snapshot_id,),
        order_by="artifact_ordinal",
    )
    quality = _rows(
        connection,
        "data_quality_results",
        _QUALITY_COLUMNS,
        where="run_id=?",
        parameters=(run_id,),
        order_by="quality_result_id",
    )
    versions = _rows(
        connection,
        "stage11_eia_weekly_observation_versions",
        _VERSION_COLUMNS,
        order_by="canonical_series_id, period, correction_sequence",
    )
    current = _rows(
        connection,
        "stage11_eia_weekly_observations",
        _CURRENT_COLUMNS,
        order_by="canonical_series_id, period",
    )
    checkpoints = _rows(
        connection,
        "dataset_registry",
        _CHECKPOINT_COLUMNS,
        where="dataset_id IN (?, ?)",
        parameters=_OUTPUT_DATASET_IDS,
        order_by="dataset_id",
    )
    if connection.execute(
        "SELECT 1 FROM ingestion_run_failures WHERE run_id=?", (run_id,)
    ).fetchone() is not None:
        raise ConflictError("Stage 11 weekly retained source has a failed run collision")
    bundle = _Bundle(
        capture=capture,
        run=run,
        outputs=outputs,
        artifacts=artifacts,
        snapshot=snapshot,
        memberships=memberships,
        quality=quality,
        versions=versions,
        current=current,
        checkpoints=checkpoints,
    )
    return bundle


def _source_bundle(
    connection: sqlite3.Connection,
    expectation: Stage11WeeklySourceExpectation,
) -> _Bundle:
    bundle = _read_source_bundle(connection)
    _validate_source_bundle(bundle, expectation)
    return bundle


def _validate_source_bundle(
    bundle: _Bundle,
    expectation: Stage11WeeklySourceExpectation,
) -> None:
    capture = bundle.capture
    capture_row = capture.one("capture")
    run = bundle.run
    run_row = run.one("ingestion run")
    artifact = bundle.artifacts.one("artifact")
    snapshot = bundle.snapshot.one("snapshot")

    if (
        capture.field(capture_row, "dataset_id") != _EVIDENCE_DATASET_ID
        or capture.field(capture_row, "provider") != "eia"
        or capture.field(capture_row, "endpoint_path") != "/v2/seriesid/PET.WCESTUS1.W"
        or capture.field(capture_row, "provider_series_id") != _PROVIDER_SERIES_ID
        or capture.field(capture_row, "completeness") != "complete"
        or capture.field(capture_row, "availability_basis") != "local_capture"
        or capture.field(capture_row, "captured_precision") != "datetime"
        or capture.field(capture_row, "normalization_version") != "stage11.eia.weekly.v1"
        or capture.field(capture_row, "row_count") != expectation.current_count
    ):
        raise ConflictError("Stage 11 weekly retained capture does not match the reviewed cohort")
    response = capture.field(capture_row, "response_bytes")
    response_sha256 = capture.field(capture_row, "response_sha256")
    request_scope = capture.field(capture_row, "request_scope_json")
    request_scope_sha256 = capture.field(capture_row, "request_scope_sha256")
    if (
        not isinstance(response, bytes)
        or hashlib.sha256(response).hexdigest() != expectation.response_sha256
        or response_sha256 != expectation.response_sha256
        or not isinstance(capture.field(capture_row, "semantic_identity"), str)
        or _SHA256.fullmatch(str(capture.field(capture_row, "semantic_identity"))) is None
        or not isinstance(request_scope, str)
        or hashlib.sha256(request_scope.encode("utf-8")).hexdigest() != request_scope_sha256
    ):
        raise ConflictError("Stage 11 weekly retained evidence digest is invalid")
    scope = _json_object(request_scope, "retained capture scope")
    if scope != {
        "endpoint_path": "/v2/seriesid/PET.WCESTUS1.W",
        "frequency": "weekly",
        "provider": "eia",
        "series_id": _PROVIDER_SERIES_ID,
    }:
        raise ConflictError("Stage 11 weekly retained capture scope is invalid")

    if (
        run.field(run_row, "dataset_id") != _CANONICAL_DATASET_ID
        or not isinstance(run.field(run_row, "semantic_identity"), str)
        or _SHA256.fullmatch(str(run.field(run_row, "semantic_identity"))) is None
        or run.field(run_row, "command") != "eia.macro.stage11_petroleum_weekly_stock_history"
        or run.field(run_row, "status") != "succeeded"
        or run.field(run_row, "fetched_count") != expectation.current_count
        or run.field(run_row, "written_count") != expectation.version_count + 1
        or run.field(run_row, "artifact_id") != capture.field(capture_row, "artifact_id")
        or run.field(run_row, "completed_at") != run.field(run_row, "started_at")
        or run.field(run_row, "warnings_json") != "[]"
        or run.field(run_row, "code_version") != "stage11.0.0"
    ):
        raise ConflictError("Stage 11 weekly retained run does not match the reviewed cohort")
    run_scope = _json_object(run.field(run_row, "scope_json"), "retained run scope")
    if run_scope != {**scope, "availability_basis": "local_capture"}:
        raise ConflictError("Stage 11 weekly retained run scope is invalid")

    if (
        len(bundle.outputs.values) != 2
        or {row[1] for row in bundle.outputs.values} != set(_OUTPUT_DATASET_IDS)
        or any(row[0] != bundle.run_id or row[2] != bundle.semantic_identity for row in bundle.outputs.values)
        or len(bundle.artifacts.values) != 1
        or artifact[bundle.artifacts.columns.index("artifact_id")]
        != capture.field(capture_row, "artifact_id")
        or artifact[bundle.artifacts.columns.index("dataset_id")] != _EVIDENCE_DATASET_ID
        or artifact[bundle.artifacts.columns.index("content_sha256")] != expectation.response_sha256
        or artifact[bundle.artifacts.columns.index("byte_count")] != len(response)
        or artifact[bundle.artifacts.columns.index("media_type")] != "application/json"
        or artifact[bundle.artifacts.columns.index("source_reference")]
        != "eia/petroleum_weekly_stock_history"
        or len(bundle.snapshot.values) != 1
        or snapshot[bundle.snapshot.columns.index("snapshot_id")]
        != run.field(run_row, "snapshot_id")
        or snapshot[bundle.snapshot.columns.index("dataset_id")] != _CANONICAL_DATASET_ID
        or snapshot[bundle.snapshot.columns.index("semantic_identity")] != bundle.semantic_identity
        or snapshot[bundle.snapshot.columns.index("completeness")] != "complete"
        or snapshot[bundle.snapshot.columns.index("row_count")] != expectation.current_count
        or snapshot[bundle.snapshot.columns.index("validation_state")] != "validated"
        or len(bundle.memberships.values) != 1
        or bundle.memberships.values[0]
        != (
            run.field(run_row, "snapshot_id"),
            capture.field(capture_row, "artifact_id"),
            1,
        )
        or len(bundle.quality.values) != 2
    ):
        raise ConflictError("Stage 11 weekly retained control-plane lineage is incomplete")

    artifact_scope = _json_object(
        artifact[bundle.artifacts.columns.index("request_scope_json")],
        "retained artifact scope",
    )
    snapshot_scope = _json_object(
        snapshot[bundle.snapshot.columns.index("scope_json")],
        "retained snapshot scope",
    )
    if artifact_scope != scope or snapshot_scope != run_scope:
        raise ConflictError("Stage 11 weekly retained control-plane scopes are invalid")
    for quality in bundle.quality.values:
        fields = dict(zip(bundle.quality.columns, quality, strict=True))
        if (
            fields["run_id"] != bundle.run_id
            or fields["outcome"] != "passed"
            or fields["severity"] != "critical"
            or fields["subject_kind"] != "snapshot"
            or fields["subject_id"] != run.field(run_row, "snapshot_id")
            or fields["snapshot_id"] != run.field(run_row, "snapshot_id")
        ):
            raise ConflictError("Stage 11 weekly retained quality lineage is invalid")

    if (
        len(bundle.current.values) != expectation.current_count
        or len(bundle.versions.values) != expectation.version_count
        or len(bundle.checkpoints.values) != 2
    ):
        raise ConflictError("Stage 11 weekly retained counts are invalid")
    current_periods = tuple(row[1] for row in bundle.current.values)
    if (
        not current_periods
        or current_periods[0] != expectation.first_period
        or current_periods[-1] != expectation.last_period
        or len(set(current_periods)) != len(current_periods)
    ):
        raise ConflictError("Stage 11 weekly retained coverage is invalid")
    version_ids: dict[str, tuple[object, ...]] = {}
    versions_by_period: dict[str, tuple[object, ...]] = {}
    for version in bundle.versions.values:
        fields = dict(zip(bundle.versions.columns, version, strict=True))
        period = fields["period"]
        version_id = fields["version_id"]
        if (
            not isinstance(period, str)
            or _DATE.fullmatch(period) is None
            or not isinstance(version_id, str)
            or not version_id
            or fields["canonical_series_id"] != _CANONICAL_SERIES_ID
            or fields["provider_series_id"] != _PROVIDER_SERIES_ID
            or fields["correction_sequence"] != 1
            or fields["supersedes_version_id"] is not None
            or fields["capture_id"] != capture.field(capture_row, "capture_id")
            or fields["artifact_id"] != capture.field(capture_row, "artifact_id")
            or fields["snapshot_id"] != capture.field(capture_row, "snapshot_id")
            or fields["run_id"] != bundle.run_id
            or fields["available_precision"] != "datetime"
            or fields["captured_precision"] != "datetime"
        ):
            raise ConflictError("Stage 11 weekly retained version lineage is invalid")
        if period in versions_by_period:
            raise ConflictError("Stage 11 weekly retained version identity is duplicated")
        versions_by_period[period] = version
        version_ids[str(version_id)] = version
    if tuple(versions_by_period) != current_periods:
        raise ConflictError("Stage 11 weekly retained current coverage is invalid")
    for current in bundle.current.values:
        series_id, period, version_id = current
        if (
            series_id != _CANONICAL_SERIES_ID
            or period not in versions_by_period
            or version_id not in version_ids
            or versions_by_period[period][0] != version_id
        ):
            raise ConflictError("Stage 11 weekly retained current pointer is invalid")
    for checkpoint in bundle.checkpoints.values:
        fields = dict(zip(bundle.checkpoints.columns, checkpoint, strict=True))
        if (
            fields["dataset_id"] not in _OUTPUT_DATASET_IDS
            or fields["store_role"] != StoreRole.MACRO.value
            or fields["active"] != 1
            or fields["last_successful_run_id"] != bundle.run_id
            or fields["last_semantic_identity"] != bundle.semantic_identity
        ):
            raise ConflictError("Stage 11 weekly retained dataset checkpoint is invalid")
    if _bundle_sha256(bundle) != expectation.bundle_sha256:
        raise ConflictError("Stage 11 weekly retained bundle digest is invalid")


def _target_bundle(connection: sqlite3.Connection, source: _Bundle) -> _Bundle:
    run_id = source.run_id
    run_row = source.run.one("run")
    snapshot_id = source.run.field(run_row, "snapshot_id")
    return _Bundle(
        capture=_rows(connection, "stage11_eia_weekly_captures", _CAPTURE_COLUMNS, order_by="capture_id"),
        run=_rows(
            connection,
            "ingestion_runs",
            _RUN_COLUMNS,
            where="run_id=?",
            parameters=(run_id,),
            order_by="run_id",
        ),
        outputs=_rows(
            connection,
            "ingestion_run_outputs",
            _OUTPUT_COLUMNS,
            where="run_id=?",
            parameters=(run_id,),
            order_by="dataset_id",
        ),
        artifacts=_rows(
            connection,
            "ingestion_artifacts",
            _ARTIFACT_COLUMNS,
            where="run_id=?",
            parameters=(run_id,),
            order_by="artifact_id",
        ),
        snapshot=_rows(
            connection,
            "ingestion_snapshots",
            _SNAPSHOT_COLUMNS,
            where="run_id=?",
            parameters=(run_id,),
            order_by="snapshot_id",
        ),
        memberships=_rows(
            connection,
            "ingestion_snapshot_artifacts",
            _MEMBERSHIP_COLUMNS,
            where="snapshot_id=?",
            parameters=(snapshot_id,),
            order_by="artifact_ordinal",
        ),
        quality=_rows(
            connection,
            "data_quality_results",
            _QUALITY_COLUMNS,
            where="run_id=?",
            parameters=(run_id,),
            order_by="quality_result_id",
        ),
        versions=_rows(
            connection,
            "stage11_eia_weekly_observation_versions",
            _VERSION_COLUMNS,
            order_by="canonical_series_id, period, correction_sequence",
        ),
        current=_rows(
            connection,
            "stage11_eia_weekly_observations",
            _CURRENT_COLUMNS,
            order_by="canonical_series_id, period",
        ),
        checkpoints=_rows(
            connection,
            "dataset_registry",
            _CHECKPOINT_COLUMNS,
            where="dataset_id IN (?, ?)",
            parameters=_OUTPUT_DATASET_IDS,
            order_by="dataset_id",
        ),
    )


def _same_rows(left: _Rows, right: _Rows) -> bool:
    return left.columns == right.columns and left.values == right.values


def _target_state(connection: sqlite3.Connection, source: _Bundle) -> str:
    _require_macro_store(connection, "canonical target")
    _require_integrity(connection, "canonical target")
    target = _target_bundle(connection, source)
    source_semantic = source.semantic_identity
    existing_semantic = connection.execute(
        """
        SELECT run_id FROM ingestion_runs
        WHERE dataset_id=? AND semantic_identity=?
        """,
        (_CANONICAL_DATASET_ID, source_semantic),
    ).fetchone()
    existing_snapshot = connection.execute(
        """
        SELECT snapshot_id FROM ingestion_snapshots
        WHERE dataset_id=? AND semantic_identity=?
        """,
        (_CANONICAL_DATASET_ID, source_semantic),
    ).fetchone()
    failed = connection.execute(
        "SELECT 1 FROM ingestion_run_failures WHERE run_id=?", (source.run_id,)
    ).fetchone()
    source_artifact_id = source.artifacts.field(
        source.artifacts.one("artifact"), "artifact_id"
    )
    source_snapshot_id = source.snapshot.field(
        source.snapshot.one("snapshot"), "snapshot_id"
    )
    quality_ids = tuple(
        str(source.quality.field(row, "quality_result_id"))
        for row in source.quality.values
    )
    artifact_collision = _rows(
        connection,
        "ingestion_artifacts",
        _ARTIFACT_COLUMNS,
        where="artifact_id=?",
        parameters=(source_artifact_id,),
        order_by="artifact_id",
    )
    snapshot_collision = _rows(
        connection,
        "ingestion_snapshots",
        _SNAPSHOT_COLUMNS,
        where="snapshot_id=?",
        parameters=(source_snapshot_id,),
        order_by="snapshot_id",
    )
    quality_collision = _rows(
        connection,
        "data_quality_results",
        _QUALITY_COLUMNS,
        where="quality_result_id IN (?, ?)",
        parameters=quality_ids,
        order_by="quality_result_id",
    )
    for actual, expected in (
        (artifact_collision, source.artifacts),
        (snapshot_collision, source.snapshot),
        (quality_collision, source.quality),
    ):
        if actual.values and not _same_rows(actual, expected):
            raise ConflictError("Stage 11 weekly canonical control-plane identity conflicts")
    source_rows = (
        source.capture,
        source.run,
        source.outputs,
        source.artifacts,
        source.snapshot,
        source.memberships,
        source.quality,
        source.versions,
        source.current,
    )
    target_rows = (
        target.capture,
        target.run,
        target.outputs,
        target.artifacts,
        target.snapshot,
        target.memberships,
        target.quality,
        target.versions,
        target.current,
    )
    source_checkpoints = source.checkpoints
    target_checkpoints = target.checkpoints
    if len(target_checkpoints.values) != 2:
        raise ConflictError("Stage 11 weekly canonical dataset declarations are unavailable")
    if any(
        tuple(row[1:6]) != tuple(source_row[1:6])
        for row, source_row in zip(target_checkpoints.values, source_checkpoints.values, strict=True)
    ):
        raise ConflictError("Stage 11 weekly canonical dataset declarations differ from source")

    empty = (
        all(not rows.values for rows in target_rows[:-1])
        and not target.current.values
        and all(row[6] is None and row[7] is None for row in target_checkpoints.values)
        and existing_semantic is None
        and existing_snapshot is None
        and failed is None
        and not artifact_collision.values
        and not snapshot_collision.values
        and not quality_collision.values
    )
    if empty:
        return "empty"

    complete = (
        all(_same_rows(left, right) for left, right in zip(source_rows, target_rows, strict=True))
        and _same_rows(source_checkpoints, target_checkpoints)
        and existing_semantic is not None
        and existing_semantic[0] == source.run_id
        and existing_snapshot is not None
        and existing_snapshot[0] == source.run.field(source.run.one("run"), "snapshot_id")
        and failed is None
    )
    if complete:
        return "complete"
    raise ConflictError("Stage 11 weekly canonical state is partial or conflicts with retained source")


def _insert_rows(connection: sqlite3.Connection, table: str, rows: _Rows) -> None:
    if not rows.values:
        return
    placeholders = ", ".join("?" for _ in rows.columns)
    connection.executemany(
        f"INSERT INTO {table} ({', '.join(rows.columns)}) VALUES ({placeholders})",
        rows.values,
    )


def _insert_bundle(connection: sqlite3.Connection, bundle: _Bundle) -> None:
    """Insert a source-verified historical run while preserving its identities."""

    source_run = bundle.run.one("run")
    fields = dict(zip(bundle.run.columns, source_run, strict=True))
    connection.execute(
        """
        INSERT INTO ingestion_runs (
            run_id, dataset_id, semantic_identity, command, scope_json, status,
            started_at, fetched_count, code_version
        ) VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?)
        """,
        (
            fields["run_id"],
            fields["dataset_id"],
            fields["semantic_identity"],
            fields["command"],
            fields["scope_json"],
            fields["started_at"],
            fields["fetched_count"],
            fields["code_version"],
        ),
    )
    _insert_rows(connection, "ingestion_run_outputs", bundle.outputs)
    _insert_rows(connection, "ingestion_artifacts", bundle.artifacts)
    _insert_rows(connection, "ingestion_snapshots", bundle.snapshot)
    _insert_rows(connection, "ingestion_snapshot_artifacts", bundle.memberships)
    _insert_rows(connection, "stage11_eia_weekly_captures", bundle.capture)
    _insert_rows(connection, "stage11_eia_weekly_observation_versions", bundle.versions)
    _insert_rows(connection, "stage11_eia_weekly_observations", bundle.current)
    _insert_rows(connection, "data_quality_results", bundle.quality)
    connection.execute(
        """
        UPDATE ingestion_runs
        SET status='succeeded', completed_at=?, artifact_id=?, snapshot_id=?,
            written_count=?, warnings_json=?
        WHERE run_id=? AND status='running'
        """,
        (
            fields["completed_at"],
            fields["artifact_id"],
            fields["snapshot_id"],
            fields["written_count"],
            fields["warnings_json"],
            fields["run_id"],
        ),
    )
    for checkpoint in bundle.checkpoints.values:
        fields = dict(zip(bundle.checkpoints.columns, checkpoint, strict=True))
        cursor = connection.execute(
            """
            UPDATE dataset_registry
            SET last_successful_run_id=?, last_semantic_identity=?
            WHERE dataset_id=?
            """,
            (
                fields["last_successful_run_id"],
                fields["last_semantic_identity"],
                fields["dataset_id"],
            ),
        )
        if cursor.rowcount != 1:
            raise ConflictError("Stage 11 weekly canonical dataset checkpoint is unavailable")


class Stage11WeeklyAdoptionRunner:
    """Injected local-only runner for one retained weekly evidence bundle."""

    def __init__(
        self,
        *,
        retained_source: str | Path,
        target_store_map: StoreMap,
        expectation: Stage11WeeklySourceExpectation,
        lock_timeout_seconds: float = 5.0,
    ) -> None:
        if not isinstance(target_store_map, StoreMap):
            raise ValidationError("Stage 11 weekly adoption requires explicit target stores")
        if not isinstance(expectation, Stage11WeeklySourceExpectation):
            raise ValidationError("Stage 11 weekly adoption requires a source expectation")
        self._source = _resolve_regular(retained_source, "retained source")
        self._target_map = target_store_map
        self._target = _resolve_regular(target_store_map.path(StoreRole.MACRO), "canonical target")
        self._expectation = expectation
        self._timeout_seconds = _require_timeout(lock_timeout_seconds)
        self._target_map.validate_distinct()
        for _, candidate in self._target_map.items():
            if candidate.exists() and os.path.samefile(self._source, candidate):
                raise ConflictError("Stage 11 weekly retained source aliases a target store")

    def run(self) -> Stage11WeeklyAdoptionReport:
        # The retained source is not in the write set.  Holding a StoreWriteLock
        # there would create a lock file beside the retained candidate, which is
        # itself a forbidden source mutation.  Its immutable read and stamps are
        # instead checked before the sole target-store write lock is acquired.
        with _source_connection(self._source) as source_connection:
            source = _source_bundle(source_connection, self._expectation)
        with StoreWriteLock(self._target, timeout_seconds=self._timeout_seconds):
            with writer_connection(self._target_map, StoreRole.MACRO) as target_connection:
                target_connection.execute("BEGIN IMMEDIATE")
                try:
                    state = _target_state(target_connection, source)
                    if state == "complete":
                        target_connection.rollback()
                        return Stage11WeeklyAdoptionReport(
                            outcome="unchanged",
                            source_current_count=len(source.current.values),
                            source_version_count=len(source.versions.values),
                            target_current_count=len(source.current.values),
                            target_version_count=len(source.versions.values),
                            inserted_capture_count=0,
                            inserted_version_count=0,
                            inserted_current_count=0,
                        )
                    _insert_bundle(target_connection, source)
                    _require_integrity(target_connection, "canonical target")
                    if _target_state(target_connection, source) != "complete":
                        raise ConflictError("Stage 11 weekly canonical verification failed")
                    target_connection.commit()
                except BaseException:
                    if target_connection.in_transaction:
                        target_connection.rollback()
                    raise
        return Stage11WeeklyAdoptionReport(
            outcome="adopted",
            source_current_count=len(source.current.values),
            source_version_count=len(source.versions.values),
            target_current_count=len(source.current.values),
            target_version_count=len(source.versions.values),
            inserted_capture_count=1,
            inserted_version_count=len(source.versions.values),
            inserted_current_count=len(source.current.values),
        )


def _canonical_store_map() -> StoreMap:
    data = PROJECT_ROOT / "data"
    return StoreMap.four_explicit(
        market=data / "market.sqlite",
        macro=CANONICAL_MACRO_STORE,
        company=data / "company.sqlite",
        news=data / "news.sqlite",
    )


def _require_canonical_binding() -> None:
    try:
        root = PROJECT_ROOT.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise StoreUnavailableError("Stage 11 weekly project root is unavailable") from exc
    if root != PROJECT_ROOT or PROJECT_ROOT.is_symlink():
        raise ValidationError("Stage 11 weekly project root binding is invalid")
    registry = load_registry(
        PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
        project_root=PROJECT_ROOT,
        environment={},
    )
    macro = registry.store(StoreRole.MACRO.value)
    if (
        registry.status != "validated"
        or macro.default_path != "data/macro.sqlite"
        or CANONICAL_MACRO_STORE != PROJECT_ROOT / macro.default_path
    ):
        raise ValidationError("Stage 11 weekly canonical target binding is invalid")


def adopt_retained_stage11_weekly_history() -> Stage11WeeklyAdoptionReport:
    """Adopt exactly the reviewed retained Stage 11 weekly history once."""

    _require_canonical_binding()
    return Stage11WeeklyAdoptionRunner(
        retained_source=RETAINED_STAGE11_WEEKLY_SOURCE,
        target_store_map=_canonical_store_map(),
        expectation=_CANONICAL_SOURCE_EXPECTATION,
    ).run()


def main(argv: list[str] | None = None) -> int:
    """Fixed, zero-argument command surface for the authorized adoption."""

    arguments = sys.argv[1:] if argv is None else list(argv)
    if arguments:
        sys.stderr.write(
            dumps_strict(
                {
                    "contract": "quant_data.stage11_weekly_adoption_error",
                    "error": "invalid_request",
                    "exit_code": 64,
                }
            )
            + "\n"
        )
        sys.stderr.flush()
        return 64
    try:
        report = adopt_retained_stage11_weekly_history()
    except ValidationError:
        code, error = 64, "invalid_request"
    except StoreUnavailableError:
        code, error = 69, "store_unavailable"
    except ConflictError:
        code, error = 75, "temporary_conflict"
    except sqlite3.Error:
        code, error = 74, "local_io"
    except Exception:
        code, error = 70, "internal_failure"
    else:
        sys.stdout.write(dumps_strict(report.mapping()) + "\n")
        sys.stdout.flush()
        return 0
    sys.stderr.write(
        dumps_strict(
            {
                "contract": "quant_data.stage11_weekly_adoption_error",
                "error": error,
                "exit_code": code,
            }
        )
        + "\n"
    )
    sys.stderr.flush()
    return code


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())


__all__ = (
    "CANONICAL_MACRO_STORE",
    "PROJECT_ROOT",
    "RETAINED_STAGE11_WEEKLY_SOURCE",
    "Stage11WeeklyAdoptionReport",
    "Stage11WeeklyAdoptionRunner",
    "Stage11WeeklySourceExpectation",
    "adopt_retained_stage11_weekly_history",
)
