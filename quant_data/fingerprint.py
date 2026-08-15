"""Canonical logical store fingerprints for read-mutation and rebuild tests.

The first implementation materialized every selected row in one Python value
and then rendered that value as JSON.  That was suitable for fixture stores,
but not for the bounded Stage 10 historical corpus: the evidence and canonical
price relations can legitimately exceed the public JSON envelope.  This module
therefore preserves the original, inspectable relation representation for small
relations and switches large relations to a compact, versioned streaming
descriptor.  The descriptor is still a deterministic fingerprint of every
column and row; it is deliberately not a lossy row-count summary.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import heapq
import os
import re
import sqlite3
import tempfile
import time
from typing import Any, BinaryIO, Iterator, Sequence

from .errors import ResourceLimitError, ValidationError
from .json_codec import dumps_strict
from .registry import Registry
from .stores import STORE_ROLES, StoreMap, StoreRole, read_connection


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CONTROL_RELATIONS = (
    "store_metadata",
    "schema_migrations",
    "dataset_registry",
    "dataset_identity_contracts",
    "ingestion_runs",
    "ingestion_run_outputs",
    "ingestion_run_failures",
    "ingestion_artifacts",
    "ingestion_snapshots",
    "ingestion_snapshot_artifacts",
    "data_quality_results",
)
_EXCLUDED_COLUMNS = {"applied_at", "registered_at", "started_at", "completed_at"}
_MAX_FINGERPRINT_JSON_BYTES = 256 * 1024 * 1024

# Keep the legacy public manifest for normal fixture-sized relations.  The
# explicit byte cap means that a relation cannot silently push an all-store
# envelope over the legacy 256 MiB internal limit.  The Stage 10 price tables
# exceed this row threshold and use the compact representation from their first
# scan, so they are never built as a giant Python list.
_LEGACY_RELATION_MAX_ROWS = 1_024
_LEGACY_RELATION_MAX_BYTES = 4 * 1024 * 1024
_LEGACY_MANIFEST_MAX_BYTES = 32 * 1024 * 1024

# Reviewed Stage 10 bounds allow at most 800 instruments with 30,000 daily
# rows each.  A price-version and a current-price relation can each therefore
# hold 24,000,000 rows.  The aggregate cap leaves room for both such relations
# and control/evidence rows while retaining a fail-closed bound for malformed
# or substituted stores.  BLOB values are represented by digest, so a 1 MiB
# canonical row cap is ample for the reviewed schemas without permitting an
# unbounded TEXT value.  These are execution budgets, not source-data limits.
_MAX_RELATION_ROWS = 25_000_000
_MAX_TOTAL_ROWS = 50_000_000
_MAX_CANONICAL_ROW_BYTES = 1 * 1024 * 1024
_MAX_TOTAL_CANONICAL_BYTES = 16 * 1024 * 1024 * 1024
_MAX_FINGERPRINT_SECONDS = 20 * 60
_SQLITE_PROGRESS_OPCODES = 10_000
_COMPACT_RELATION_FORMAT = "quant_data.sqlite_relation_fingerprint.v2"
_COMPACT_RELATION_HASH_DOMAIN = b"quant-data/sqlite-relation-fingerprint/v2\x00"
_SORT_CHUNK_MAX_ROWS = 100_000
_SORT_CHUNK_MAX_BYTES = 64 * 1024 * 1024
_SORT_MERGE_FAN_IN = 32
_SORT_FRAME_BYTES = 4


def _fingerprint_digest(value: Any) -> str:
    """Hash a bounded internal manifest larger than the public JSON envelope.

    Small manifests intentionally retain the pre-Stage-10 canonical JSON hash.
    Large relations are compacted before this helper receives the outer
    manifest, so callers do not need to materialize a multi-hundred-megabyte
    JSON value merely to compute its digest.
    """

    return hashlib.sha256(
        dumps_strict(value, max_bytes=_MAX_FINGERPRINT_JSON_BYTES).encode("utf-8")
    ).hexdigest()


def _fingerprint_value(value: Any) -> Any:
    """Render SQLite BLOBs as bounded, deterministic strict-JSON values."""

    if isinstance(value, (bytes, bytearray, memoryview)):
        payload = bytes(value)
        return {
            "sqlite_blob_byte_count": len(payload),
            "sqlite_blob_sha256": hashlib.sha256(payload).hexdigest(),
        }
    return value


def _quoted_identifier(identifier: str) -> str:
    """Return one schema-derived identifier only after a strict allow-list."""

    if not _IDENTIFIER.fullmatch(identifier):
        raise ValidationError("Unsafe relation in fingerprint contract")
    return f'"{identifier}"'


class _FingerprintBudget:
    """One deterministic resource budget for a store or all-store operation."""

    def __init__(self) -> None:
        self._started = time.monotonic()
        self._deadline = self._started + _MAX_FINGERPRINT_SECONDS
        self._total_rows = 0
        self._total_canonical_bytes = 0
        self._legacy_manifest_bytes = 0
        self.expired = False

    def check_time(self) -> None:
        if time.monotonic() > self._deadline:
            self.expired = True
            raise ResourceLimitError("Fingerprint execution exceeded the reviewed time limit")

    def progress(self) -> int:
        """SQLite progress callback; nonzero interrupts the current statement."""

        if time.monotonic() > self._deadline:
            self.expired = True
            return 1
        return 0

    def begin_relation(self, relation: str, expected_rows: int) -> None:
        self.check_time()
        if expected_rows < 0 or expected_rows > _MAX_RELATION_ROWS:
            raise ResourceLimitError(
                f"Fingerprint relation {relation!r} exceeds the reviewed row limit"
            )

    def observe_row(self, relation: str, row_bytes: int) -> None:
        if row_bytes > _MAX_CANONICAL_ROW_BYTES:
            raise ResourceLimitError(
                f"Fingerprint row in relation {relation!r} exceeds the reviewed byte limit"
            )
        self._total_rows += 1
        self._total_canonical_bytes += row_bytes
        if self._total_rows > _MAX_TOTAL_ROWS:
            raise ResourceLimitError("Fingerprint exceeds the reviewed total row limit")
        if self._total_canonical_bytes > _MAX_TOTAL_CANONICAL_BYTES:
            raise ResourceLimitError("Fingerprint exceeds the reviewed total byte limit")
        # Avoid a system-call per normal row while still enforcing a bounded
        # deadline during a Python-level scan.
        if self._total_rows % 1_024 == 0:
            self.check_time()

    def legacy_capacity(self) -> int:
        return max(0, _LEGACY_MANIFEST_MAX_BYTES - self._legacy_manifest_bytes)

    def reserve_legacy_bytes(self, byte_count: int) -> None:
        if byte_count < 0 or byte_count > self.legacy_capacity():
            raise ResourceLimitError("Fingerprint legacy manifest budget is invalid")
        self._legacy_manifest_bytes += byte_count


@contextmanager
def _bounded_read(connection: sqlite3.Connection, budget: _FingerprintBudget) -> Iterator[None]:
    """Install a deadline guard without changing the query-only connection."""

    connection.set_progress_handler(budget.progress, _SQLITE_PROGRESS_OPCODES)
    try:
        yield
    except sqlite3.OperationalError as exc:
        if budget.expired:
            raise ResourceLimitError(
                "Fingerprint execution exceeded the reviewed time limit"
            ) from exc
        raise
    finally:
        connection.set_progress_handler(None, 0)


def _relation_columns(
    connection: sqlite3.Connection,
    relation: str,
    *,
    exclude_volatile: bool,
) -> list[str]:
    quoted_relation = _quoted_identifier(relation)
    columns = [
        str(row["name"])
        for row in connection.execute(f"PRAGMA table_info({quoted_relation})")
        if not exclude_volatile or row["name"] not in _EXCLUDED_COLUMNS
    ]
    # SQLite permits arbitrary column names, but an operational store may not
    # smuggle one into an internally generated query.
    for column in columns:
        _quoted_identifier(column)
    return columns


def _relation_row_count(connection: sqlite3.Connection, relation: str) -> int:
    quoted_relation = _quoted_identifier(relation)
    value = connection.execute(f"SELECT COUNT(*) FROM {quoted_relation}").fetchone()[0]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValidationError("Fingerprint relation row count is invalid")
    return value


def _row_value(columns: list[str], row: sqlite3.Row) -> dict[str, Any]:
    return {column: _fingerprint_value(row[column]) for column in columns}


def _canonical_row_bytes(
    relation: str,
    columns: list[str],
    row: sqlite3.Row,
) -> bytes:
    rendered = dumps_strict(
        _row_value(columns, row),
        max_bytes=_MAX_CANONICAL_ROW_BYTES,
    ).encode("utf-8")
    if len(rendered) > _MAX_CANONICAL_ROW_BYTES:
        # The strict renderer normally catches this first; retain an explicit
        # invariant here because this byte count also drives the run budget.
        raise ResourceLimitError(
            f"Fingerprint row in relation {relation!r} exceeds the reviewed byte limit"
        )
    return rendered


def _select_rows_sql(relation: str, columns: list[str]) -> str:
    quoted_relation = _quoted_identifier(relation)
    quoted_columns = [_quoted_identifier(column) for column in columns]
    select = ", ".join(quoted_columns)
    return f"SELECT {select} FROM {quoted_relation}"


def _write_frame(handle: BinaryIO, payload: bytes) -> None:
    if len(payload) > _MAX_CANONICAL_ROW_BYTES:
        raise ResourceLimitError("Fingerprint sort frame exceeds the reviewed byte limit")
    handle.write(len(payload).to_bytes(_SORT_FRAME_BYTES, "big"))
    handle.write(payload)


def _read_frame(handle: BinaryIO) -> bytes | None:
    header = handle.read(_SORT_FRAME_BYTES)
    if not header:
        return None
    if len(header) != _SORT_FRAME_BYTES:
        raise ValidationError("Fingerprint sort scratch is truncated")
    byte_count = int.from_bytes(header, "big")
    if byte_count > _MAX_CANONICAL_ROW_BYTES:
        raise ResourceLimitError("Fingerprint sort frame exceeds the reviewed byte limit")
    payload = handle.read(byte_count)
    if len(payload) != byte_count:
        raise ValidationError("Fingerprint sort scratch is truncated")
    return payload


def _open_private_scratch(path: str) -> BinaryIO:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    return os.fdopen(descriptor, "wb")


def _write_sorted_chunk(path: str, rows: list[bytes]) -> None:
    rows.sort()
    with _open_private_scratch(path) as handle:
        for payload in rows:
            _write_frame(handle, payload)


def _merge_sorted_chunks(
    paths: Sequence[str],
    budget: _FingerprintBudget,
) -> Iterator[bytes]:
    handles: list[BinaryIO] = []
    heap: list[tuple[bytes, int]] = []
    emitted = 0
    try:
        for index, path in enumerate(paths):
            handle = open(path, "rb")
            handles.append(handle)
            payload = _read_frame(handle)
            if payload is not None:
                heapq.heappush(heap, (payload, index))
        while heap:
            payload, index = heapq.heappop(heap)
            yield payload
            emitted += 1
            if emitted % 1_024 == 0:
                budget.check_time()
            following = _read_frame(handles[index])
            if following is not None:
                heapq.heappush(heap, (following, index))
    finally:
        for handle in handles:
            handle.close()


def _merge_chunk_group(
    paths: Sequence[str],
    destination: str,
    budget: _FingerprintBudget,
) -> None:
    with _open_private_scratch(destination) as handle:
        for payload in _merge_sorted_chunks(paths, budget):
            _write_frame(handle, payload)


def _reduce_sorted_chunks(
    paths: list[str],
    directory: str,
    budget: _FingerprintBudget,
    next_index: int,
) -> tuple[list[str], int]:
    """Bound merge fan-in so reviewed maximums cannot exhaust file descriptors."""

    current = paths
    while len(current) > _SORT_MERGE_FAN_IN:
        following: list[str] = []
        for first in range(0, len(current), _SORT_MERGE_FAN_IN):
            group = current[first : first + _SORT_MERGE_FAN_IN]
            if len(group) == 1:
                following.append(group[0])
                continue
            destination = os.path.join(directory, f"merge-{next_index:08d}.bin")
            next_index += 1
            _merge_chunk_group(group, destination, budget)
            for path in group:
                os.unlink(path)
            following.append(destination)
        current = following
        budget.check_time()
    return current, next_index


def _compact_relation_rows(
    connection: sqlite3.Connection,
    relation: str,
    columns: list[str],
    expected_rows: int,
    budget: _FingerprintBudget,
) -> dict[str, Any]:
    """Externally sort canonical rows and stream a versioned relation digest.

    SQLite's value ordering is not a total ordering over strict-JSON values:
    for example INTEGER ``1`` and REAL ``1.0`` compare equal but render
    differently. Sorting canonical row bytes directly makes the digest a
    function of the row multiset, never physical insertion order. Private,
    bounded chunks provide that guarantee without materializing the relation.
    """

    digest = hashlib.sha256()
    digest.update(_COMPACT_RELATION_HASH_DOMAIN)
    column_bytes = dumps_strict(columns).encode("utf-8")
    digest.update(len(column_bytes).to_bytes(8, "big"))
    digest.update(column_bytes)
    row_count = 0
    canonical_row_bytes = 0
    with tempfile.TemporaryDirectory(
        prefix="quant-data-fingerprint-",
        dir="/tmp",
    ) as scratch:
        chunk: list[bytes] = []
        chunk_bytes = 0
        chunk_paths: list[str] = []
        next_index = 0
        for row in connection.execute(_select_rows_sql(relation, columns)):
            rendered = _canonical_row_bytes(relation, columns, row)
            budget.observe_row(relation, len(rendered))
            row_count += 1
            canonical_row_bytes += len(rendered)
            chunk.append(rendered)
            chunk_bytes += len(rendered)
            if (
                len(chunk) >= _SORT_CHUNK_MAX_ROWS
                or chunk_bytes >= _SORT_CHUNK_MAX_BYTES
            ):
                path = os.path.join(scratch, f"chunk-{next_index:08d}.bin")
                next_index += 1
                _write_sorted_chunk(path, chunk)
                chunk_paths.append(path)
                chunk = []
                chunk_bytes = 0
        if chunk:
            path = os.path.join(scratch, f"chunk-{next_index:08d}.bin")
            next_index += 1
            _write_sorted_chunk(path, chunk)
            chunk_paths.append(path)
        chunk_paths, next_index = _reduce_sorted_chunks(
            chunk_paths,
            scratch,
            budget,
            next_index,
        )
        merged_count = 0
        merged_bytes = 0
        for rendered in _merge_sorted_chunks(chunk_paths, budget):
            merged_count += 1
            merged_bytes += len(rendered)
            digest.update(len(rendered).to_bytes(8, "big"))
            digest.update(rendered)
        if merged_count != row_count or merged_bytes != canonical_row_bytes:
            raise ValidationError("Fingerprint canonical sort is incomplete")
    budget.check_time()
    if row_count != expected_rows:
        # The read connection holds one explicit snapshot, so this is an
        # integrity/contract error rather than a race to silently tolerate.
        raise ValidationError("Fingerprint relation changed within a read snapshot")
    digest.update(row_count.to_bytes(8, "big"))
    digest.update(canonical_row_bytes.to_bytes(16, "big"))
    return {
        "canonical_row_bytes": canonical_row_bytes,
        "columns": columns,
        "fingerprint_format": _COMPACT_RELATION_FORMAT,
        "row_count": row_count,
        "row_sha256": digest.hexdigest(),
    }


def _legacy_relation_rows(
    connection: sqlite3.Connection,
    relation: str,
    columns: list[str],
    expected_rows: int,
    budget: _FingerprintBudget,
) -> dict[str, Any] | None:
    """Build the exact pre-v2 small relation value, or request compaction.

    Returning ``None`` is a normal bounded fallback.  At most the configured
    small legacy byte budget is ever held before that fallback re-scans the
    relation in compact mode.
    """

    if (
        expected_rows > _LEGACY_RELATION_MAX_ROWS
        or budget.legacy_capacity() == 0
    ):
        return None
    values: list[tuple[str, dict[str, Any]]] = []
    byte_count = 0
    for row in connection.execute(_select_rows_sql(relation, columns)):
        rendered = _canonical_row_bytes(relation, columns, row)
        budget.observe_row(relation, len(rendered))
        byte_count += len(rendered)
        if (
            byte_count > _LEGACY_RELATION_MAX_BYTES
            or byte_count > budget.legacy_capacity()
        ):
            return None
        values.append((rendered.decode("utf-8"), _row_value(columns, row)))
    budget.check_time()
    if len(values) != expected_rows:
        raise ValidationError("Fingerprint relation changed within a read snapshot")
    values.sort(key=lambda item: item[0])
    budget.reserve_legacy_bytes(byte_count)
    return {"columns": columns, "rows": [item[1] for item in values]}


def _table_rows(
    connection: sqlite3.Connection,
    relation: str,
    *,
    exclude_volatile: bool = True,
    budget: _FingerprintBudget,
) -> dict[str, Any]:
    """Return a legacy small value or a compact v2 relation descriptor."""

    columns = _relation_columns(connection, relation, exclude_volatile=exclude_volatile)
    if not columns:
        return {"columns": [], "rows": []}
    expected_rows = _relation_row_count(connection, relation)
    budget.begin_relation(relation, expected_rows)
    legacy = _legacy_relation_rows(
        connection,
        relation,
        columns,
        expected_rows,
        budget,
    )
    if legacy is not None:
        return legacy
    return _compact_relation_rows(
        connection,
        relation,
        columns,
        expected_rows,
        budget,
    )


def _store_integrity_is_valid(connection: sqlite3.Connection) -> bool:
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    foreign_key_violation = connection.execute("PRAGMA foreign_key_check").fetchone()
    return integrity == "ok" and foreign_key_violation is None


def _schema_rows(connection: sqlite3.Connection, *, mutation: bool) -> list[dict[str, Any]]:
    relation_types = "'table', 'view', 'index', 'trigger'" if mutation else "'table', 'view', 'trigger'"
    return [
        {"type": row["type"], "name": row["name"], "sql": row["sql"]}
        for row in connection.execute(
            f"""
            SELECT type, name, sql FROM sqlite_master
            WHERE type IN ({relation_types})
              AND name NOT LIKE 'sqlite_%'
            ORDER BY type, name
            """
        )
    ]


def store_logical_manifest(
    store_map: StoreMap,
    registry: Registry,
    role: StoreRole | str,
    *,
    _budget: _FingerprintBudget | None = None,
) -> dict[str, Any]:
    normalized = StoreRole(role)
    budget = _budget if _budget is not None else _FingerprintBudget()
    relations = list(_CONTROL_RELATIONS)
    for dataset in registry.datasets_for(normalized.value):
        relations.extend(dataset.relations)
    relations = sorted(dict.fromkeys(relations))
    with read_connection(store_map, normalized) as connection:
        with _bounded_read(connection, budget):
            if not _store_integrity_is_valid(connection):
                raise ValidationError("Store integrity check failed")
            relation_data = {
                relation: _table_rows(connection, relation, budget=budget)
                for relation in relations
            }
            schema_rows = _schema_rows(connection, mutation=False)
    payload = {
        "role": normalized.value,
        "schema": schema_rows,
        "relations": relation_data,
    }
    return {
        **payload,
        "sha256": _fingerprint_digest(payload),
    }


def logical_manifest(store_map: StoreMap, registry: Registry) -> dict[str, Any]:
    budget = _FingerprintBudget()
    stores = {
        role.value: store_logical_manifest(store_map, registry, role, _budget=budget)
        for role in STORE_ROLES
    }
    registry_declarations = dict(registry.raw)
    payload = {
        "registry": registry_declarations,
        "stores": stores,
    }
    return {
        **payload,
        "sha256": _fingerprint_digest(payload),
    }


def store_mutation_fingerprint(
    store_map: StoreMap,
    role: StoreRole | str,
    *,
    _budget: _FingerprintBudget | None = None,
) -> dict[str, Any]:
    """Fingerprint every persistent store field without exclusions."""

    normalized = StoreRole(role)
    budget = _budget if _budget is not None else _FingerprintBudget()
    with read_connection(store_map, normalized) as connection:
        with _bounded_read(connection, budget):
            if not _store_integrity_is_valid(connection):
                raise ValidationError("Store integrity check failed")
            table_names = [
                str(row["name"])
                for row in connection.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name NOT LIKE 'sqlite_%'
                    ORDER BY name
                    """
                )
            ]
            relations = {
                name: _table_rows(
                    connection,
                    name,
                    exclude_volatile=False,
                    budget=budget,
                )
                for name in table_names
            }
            schema_rows = _schema_rows(connection, mutation=True)
            header = {
                "application_id": int(connection.execute("PRAGMA application_id").fetchone()[0]),
                "user_version": int(connection.execute("PRAGMA user_version").fetchone()[0]),
            }
    payload = {
        "role": normalized.value,
        "header": header,
        "schema": schema_rows,
        "relations": relations,
    }
    return {
        **payload,
        "sha256": _fingerprint_digest(payload),
    }


def mutation_fingerprint(store_map: StoreMap) -> dict[str, Any]:
    """Fingerprint all four stores for public no-write assertions."""

    budget = _FingerprintBudget()
    stores = {
        role.value: store_mutation_fingerprint(store_map, role, _budget=budget)
        for role in STORE_ROLES
    }
    payload = {"stores": stores}
    return {
        **payload,
        "sha256": _fingerprint_digest(payload),
    }
