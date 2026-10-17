"""Local, snapshot-style inspector for the authorized Stage 11 candidate.

This module is deliberately separate from the frozen Stage 6 dashboard and
from the public tool registry.  Its production target is fixed, its HTTP
surface accepts no filesystem or SQL input, and every SQLite connection is an
immutable query-only connection.  A non-empty WAL or any candidate change
while a request is running fails closed.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import re
import sqlite3
import stat
import sys
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit

from .errors import RegistryError, ResourceLimitError, ValidationError
from .json_codec import dumps_strict, loads_strict
from .macro.stage11_scope import load_stage11_macro_scope
from .operations.stage11_completion import (
    Stage11CandidateReceipt,
    Stage11CompletionEvidence,
)
from .registry import load_registry, stage11_registry_profile


APPROVED_TARGET_ROOT = Path(
    "/home/volatility/quant-data-nonprod/stage10-fmp-market-history-v1"
)
TARGET_PROFILE_ID = "stage11_bea_eia_macro_v1"
MIGRATION_ID = "macro:0012_stage11_bea_eia_live_history"
MIGRATION_ORDINAL = 12
LOOPBACK_HOST = "127.0.0.1"

_MAX_TARGET_CHARS = 4096
_MAX_MARKER_BYTES = 64 * 1024
_MAX_CANDIDATE_RECEIPT_BYTES = 512 * 1024
_PAGE_MAX = 100
_LIMIT_MAX = 100
_INTEGER = re.compile(r"[1-9][0-9]*\Z")
_ASSET_ROOT = Path(__file__).with_name("dashboard") / "static"
_MIGRATION_RESOURCE = (
    Path(__file__).with_name("migrations")
    / "macro"
    / "0012_stage11_bea_eia_live_history.sql"
)
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_SCOPE_RESOURCE = _PROJECT_ROOT / "config" / "stage11_macro_scope.json"
_REGISTRY_RESOURCE = _PROJECT_ROOT / "config" / "system_registry.json"
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")

_SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
        "form-action 'self'"
    ),
}


@dataclass(frozen=True, slots=True)
class SeriesSpec:
    slug: str
    canonical_id: str
    label: str
    family: str
    versions_relation: str
    current_relation: str
    dimensions: tuple[str, ...]
    natural_key: tuple[str, ...]


SERIES: tuple[SeriesSpec, ...] = (
    SeriesSpec(
        "gdp-real-qoq-saar-pct",
        "macro.gdp.real_qoq_saar_pct",
        "Real GDP growth",
        "BEA NIPA T10101 / A191RL",
        "stage11_bea_nipa_observation_versions",
        "stage11_bea_nipa_observations",
        ("table_name", "series_code", "unit_multiplier"),
        ("period",),
    ),
    SeriesSpec(
        "gdp-nominal-billions",
        "macro.gdp.nominal_billions",
        "Nominal GDP",
        "BEA NIPA T10105 / A191RC",
        "stage11_bea_nipa_observation_versions",
        "stage11_bea_nipa_observations",
        ("table_name", "series_code", "unit_multiplier"),
        ("period",),
    ),
    SeriesSpec(
        "electricity-retail-sales",
        "macro.eia.electricity.retail_sales",
        "Electricity retail sales",
        "EIA monthly / US / all sectors",
        "stage11_eia_retail_observation_versions",
        "stage11_eia_retail_observations",
        ("metric", "state_id", "sector_id"),
        ("period", "state_id", "sector_id"),
    ),
    SeriesSpec(
        "electricity-retail-revenue",
        "macro.eia.electricity.retail_revenue",
        "Electricity retail revenue",
        "EIA monthly / US / all sectors",
        "stage11_eia_retail_observation_versions",
        "stage11_eia_retail_observations",
        ("metric", "state_id", "sector_id"),
        ("period", "state_id", "sector_id"),
    ),
    SeriesSpec(
        "electricity-retail-price",
        "macro.eia.electricity.retail_price",
        "Electricity retail price",
        "EIA monthly / US / all sectors",
        "stage11_eia_retail_observation_versions",
        "stage11_eia_retail_observations",
        ("metric", "state_id", "sector_id"),
        ("period", "state_id", "sector_id"),
    ),
    SeriesSpec(
        "electricity-retail-customers",
        "macro.eia.electricity.retail_customers",
        "Electricity retail customers",
        "EIA monthly / US / all sectors",
        "stage11_eia_retail_observation_versions",
        "stage11_eia_retail_observations",
        ("metric", "state_id", "sector_id"),
        ("period", "state_id", "sector_id"),
    ),
    SeriesSpec(
        "petroleum-weekly-stock",
        "macro.eia.weekly.petroleum_stock",
        "Weekly petroleum stock series",
        "EIA weekly / PET.WCESTUS1.W",
        "stage11_eia_weekly_observation_versions",
        "stage11_eia_weekly_observations",
        ("provider_series_id",),
        ("period",),
    ),
)
_SERIES_BY_SLUG = {item.slug: item for item in SERIES}
_APPROVED_CANONICAL_IDS = frozenset(item.canonical_id for item in SERIES)

_CAPTURE_RELATIONS = (
    "stage11_bea_nipa_captures",
    "stage11_eia_retail_captures",
    "stage11_eia_weekly_captures",
)
_REQUIRED_RELATIONS = frozenset(
    _CAPTURE_RELATIONS
    + tuple(item.versions_relation for item in SERIES)
    + tuple(item.current_relation for item in SERIES)
)
_REQUIRED_VERSION_COLUMNS = frozenset(
    {
        "version_id",
        "canonical_series_id",
        "period",
        "value_text",
        "unit",
        "available_at",
        "available_precision",
        "captured_at",
        "captured_precision",
        "correction_sequence",
        "capture_id",
    }
)
_REQUIRED_CURRENT_COLUMNS = frozenset(
    {"canonical_series_id", "period", "current_version_id"}
)


class InspectorError(Exception):
    status = HTTPStatus.INTERNAL_SERVER_ERROR
    code = "inspector_error"
    public_message = "The candidate inspector could not complete the request."


class InspectorConfigurationError(InspectorError):
    status = HTTPStatus.SERVICE_UNAVAILABLE
    code = "candidate_unavailable"
    public_message = "The reviewed candidate snapshot is unavailable."


class InspectorConflictError(InspectorError):
    status = HTTPStatus.CONFLICT
    code = "candidate_changed"
    public_message = "The candidate changed; restart the inspector before reading it."


class InspectorValidationError(InspectorError):
    status = HTTPStatus.BAD_REQUEST
    code = "invalid_request"
    public_message = "The inspector request is invalid."


class InspectorResourceError(InspectorError):
    status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    code = "resource_limit"
    public_message = "The inspector request exceeds the reviewed limit."


class InspectorNotFoundError(InspectorError):
    status = HTTPStatus.NOT_FOUND
    code = "not_found"
    public_message = "The inspector route was not found."


class InspectorMethodError(InspectorError):
    status = HTTPStatus.METHOD_NOT_ALLOWED
    code = "method_not_allowed"
    public_message = "The HTTP method is not supported."


@dataclass(frozen=True, slots=True)
class InspectorResponse:
    status: int
    body: bytes
    content_type: str


@dataclass(frozen=True, slots=True)
class FileStamp:
    present: bool
    device: int | None = None
    inode: int | None = None
    mode: int | None = None
    size: int | None = None
    mtime_ns: int | None = field(default=None, compare=False)
    sha256: str | None = None


def _hash_regular_file(path: Path, *, required: bool) -> FileStamp:
    try:
        before = path.lstat()
    except FileNotFoundError:
        if required:
            raise InspectorConfigurationError() from None
        return FileStamp(False)
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise InspectorConfigurationError()
    digest = hashlib.sha256()
    try:
        with path.open("rb", buffering=0) as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        after = path.lstat()
    except (OSError, PermissionError):
        raise InspectorConfigurationError() from None
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
    )
    if identity_before != identity_after:
        raise InspectorConflictError()
    return FileStamp(
        True,
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
        digest.hexdigest(),
    )


def _require_directory(path: Path) -> None:
    try:
        status = path.lstat()
    except (FileNotFoundError, OSError):
        raise InspectorConfigurationError() from None
    if stat.S_ISLNK(status.st_mode) or not stat.S_ISDIR(status.st_mode):
        raise InspectorConfigurationError()


def _strict_int(value: str | None, *, default: int, maximum: int) -> int:
    if value is None:
        return default
    if not _INTEGER.fullmatch(value):
        raise InspectorValidationError()
    parsed = int(value)
    if parsed > maximum:
        raise InspectorResourceError()
    return parsed


def _parse_query(raw: str, *, allowed: frozenset[str]) -> dict[str, str]:
    if len(raw) > 2048:
        raise InspectorResourceError()
    if not raw:
        return {}
    try:
        pairs = parse_qsl(
            raw,
            keep_blank_values=True,
            strict_parsing=True,
            errors="strict",
            max_num_fields=8,
        )
    except (ValueError, UnicodeError):
        raise InspectorValidationError() from None
    result: dict[str, str] = {}
    for key, value in pairs:
        if key in result or key not in allowed:
            raise InspectorValidationError()
        if len(key) > 64 or len(value) > 128:
            raise InspectorResourceError()
        result[key] = value
    return result


class Stage11CandidateInspector:
    """Fixed read surface over one immutable view of the approved candidate."""

    def __init__(self) -> None:
        root = APPROVED_TARGET_ROOT
        if not root.is_absolute() or root == Path(root.anchor) or root == Path.home():
            raise InspectorConfigurationError()
        _require_directory(root)
        _require_directory(root / "stores")
        self._root = root
        self._database = root / "stores" / "macro.sqlite"
        self._wal = Path(str(self._database) + "-wal")
        self._shm = Path(str(self._database) + "-shm")
        self._resume = root / "private" / "candidate" / "stage11" / "resume.json"
        self._completion = (
            root / "private" / "candidate" / "stage11" / "completion.json"
        )
        _hash_regular_file(_SCOPE_RESOURCE, required=True)
        registry_stamp = _hash_regular_file(_REGISTRY_RESOURCE, required=True)
        try:
            scope = load_stage11_macro_scope(_SCOPE_RESOURCE)
            registry = stage11_registry_profile(
                load_registry(
                    _REGISTRY_RESOURCE,
                    project_root=_PROJECT_ROOT,
                    environment={},
                )
            )
        except (RegistryError, ResourceLimitError, ValidationError):
            raise InspectorConfigurationError() from None
        if (
            scope.target_profile_id != TARGET_PROFILE_ID
            or registry_stamp.sha256 is None
        ):
            raise InspectorConfigurationError()
        self._scope_manifest_sha256 = scope.manifest_sha256
        self._registry_source_sha256 = registry.source_sha256
        self._candidate_receipt_directory = (
            self._completion.parent / "candidate" / "promotion-candidates"
        )
        self._candidate_receipt = self._candidate_receipt_directory / (
            f"stage11-bea-eia-macro-v1-{scope.manifest_sha256[:16]}.json"
        )
        before = self._snapshot()
        self._validate_database()
        self._marker_state()
        after = self._snapshot()
        if before != after:
            raise InspectorConflictError()
        self._baseline = after

    def _snapshot(self) -> tuple[FileStamp, ...]:
        database = _hash_regular_file(self._database, required=True)
        wal = _hash_regular_file(self._wal, required=False)
        if wal.present and wal.size != 0:
            raise InspectorConflictError()
        # SQLite may create an empty WAL and a volatile SHM file for a
        # concurrent read-only connection.  Neither belongs to the immutable
        # main-database snapshot: validate their file types, reject any WAL
        # content, and normalize harmless presence/SHM changes.
        _hash_regular_file(self._shm, required=False)
        inactive_auxiliary = FileStamp(False)
        return (
            database,
            inactive_auxiliary,
            inactive_auxiliary,
            _hash_regular_file(self._resume, required=False),
            _hash_regular_file(self._completion, required=False),
            _hash_regular_file(self._candidate_receipt, required=False),
        )

    def _connect(self) -> sqlite3.Connection:
        uri = self._database.as_uri() + "?mode=ro&immutable=1"
        try:
            connection = sqlite3.connect(
                uri,
                uri=True,
                timeout=5.0,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA foreign_keys=ON")
            return connection
        except sqlite3.Error:
            raise InspectorConfigurationError() from None

    def _validate_database(self) -> None:
        if not _MIGRATION_RESOURCE.is_file():
            raise InspectorConfigurationError()
        expected_sha = hashlib.sha256(_MIGRATION_RESOURCE.read_bytes()).hexdigest()
        connection = self._connect()
        try:
            quick = tuple(connection.execute("PRAGMA quick_check"))
            if len(quick) != 1 or quick[0][0] != "ok":
                raise InspectorConfigurationError()
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise InspectorConfigurationError()
            role = connection.execute(
                "SELECT store_role FROM store_metadata WHERE singleton=1"
            ).fetchone()
            if role is None or role["store_role"] != "macro":
                raise InspectorConfigurationError()
            migration = connection.execute(
                """
                SELECT store_role, ordinal, sha256, reconstruction_state
                FROM schema_migrations
                WHERE migration_id=?
                """,
                (MIGRATION_ID,),
            ).fetchone()
            if migration is None or (
                migration["store_role"],
                migration["ordinal"],
                migration["sha256"],
                migration["reconstruction_state"],
            ) != ("macro", MIGRATION_ORDINAL, expected_sha, "fixture_validated"):
                raise InspectorConfigurationError()
            relations = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if not _REQUIRED_RELATIONS.issubset(relations):
                raise InspectorConfigurationError()
            for relation in {item.versions_relation for item in SERIES}:
                columns = {
                    str(row["name"])
                    for row in connection.execute(f'PRAGMA table_info("{relation}")')
                }
                required = _REQUIRED_VERSION_COLUMNS | {
                    dimension
                    for item in SERIES
                    if item.versions_relation == relation
                    for dimension in item.dimensions
                }
                if not required.issubset(columns):
                    raise InspectorConfigurationError()
            for relation in {item.current_relation for item in SERIES}:
                columns = {
                    str(row["name"])
                    for row in connection.execute(f'PRAGMA table_info("{relation}")')
                }
                required = _REQUIRED_CURRENT_COLUMNS | {
                    key
                    for item in SERIES
                    if item.current_relation == relation
                    for key in item.natural_key
                }
                if not required.issubset(columns):
                    raise InspectorConfigurationError()
            observed: set[str] = set()
            for relation in {item.versions_relation for item in SERIES}:
                observed.update(
                    str(row[0])
                    for row in connection.execute(
                        f'SELECT DISTINCT canonical_series_id FROM "{relation}"'
                    )
                )
            if not observed.issubset(_APPROVED_CANONICAL_IDS):
                raise InspectorConfigurationError()
            for spec in SERIES:
                self._require_current_pointers(connection, spec)
        except sqlite3.Error:
            raise InspectorConfigurationError() from None
        finally:
            connection.close()

    @staticmethod
    def _require_current_pointers(
        connection: sqlite3.Connection,
        spec: SeriesSpec,
    ) -> None:
        coverage_match = " AND ".join(
            f'current_row."{key}"=version_row."{key}"'
            for key in spec.natural_key
        )
        unpointed = connection.execute(
            f"""
            SELECT COUNT(*)
            FROM "{spec.versions_relation}" AS version_row
            WHERE version_row.canonical_series_id=?
              AND NOT EXISTS (
                SELECT 1
                FROM "{spec.current_relation}" AS current_row
                WHERE current_row.canonical_series_id=version_row.canonical_series_id
                  AND {coverage_match}
              )
            """,
            (spec.canonical_id,),
        ).fetchone()[0]
        missing = connection.execute(
            f"""
            SELECT COUNT(*)
            FROM "{spec.current_relation}" AS current_row
            LEFT JOIN "{spec.versions_relation}" AS version_row
              ON version_row.version_id=current_row.current_version_id
            WHERE current_row.canonical_series_id=?
              AND (
                version_row.version_id IS NULL OR
                version_row.canonical_series_id<>current_row.canonical_series_id
              )
            """,
            (spec.canonical_id,),
        ).fetchone()[0]
        key_match = " AND ".join(
            f'later."{key}"=version_row."{key}"' for key in spec.natural_key
        )
        stale = connection.execute(
            f"""
            SELECT COUNT(*)
            FROM "{spec.current_relation}" AS current_row
            JOIN "{spec.versions_relation}" AS version_row
              ON version_row.version_id=current_row.current_version_id
            WHERE current_row.canonical_series_id=?
              AND EXISTS (
                SELECT 1
                FROM "{spec.versions_relation}" AS later
                WHERE later.canonical_series_id=version_row.canonical_series_id
                  AND {key_match}
                  AND later.correction_sequence>version_row.correction_sequence
              )
            """,
            (spec.canonical_id,),
        ).fetchone()[0]
        if unpointed or missing or stale:
            raise InspectorConfigurationError()

    def _guarded_read(self, reader: Callable[[sqlite3.Connection], Any]) -> Any:
        before = self._snapshot()
        if before != self._baseline:
            raise InspectorConflictError()
        connection = self._connect()
        try:
            result = reader(connection)
        except sqlite3.Error:
            raise InspectorConfigurationError() from None
        finally:
            connection.close()
        after = self._snapshot()
        if after != before:
            raise InspectorConflictError()
        return result

    @staticmethod
    def _read_marker(path: Path) -> dict[str, Any]:
        try:
            raw = loads_strict(path.read_bytes(), max_bytes=_MAX_MARKER_BYTES)
        except (OSError, ResourceLimitError, ValidationError):
            raise InspectorConfigurationError() from None
        if not isinstance(raw, dict):
            raise InspectorConfigurationError()
        return raw

    def _validate_candidate_receipt(
        self,
        completion: Mapping[str, Any],
    ) -> None:
        _require_directory(self._candidate_receipt_directory)
        try:
            entries = {
                item.name: item
                for item in self._candidate_receipt_directory.iterdir()
            }
        except OSError:
            raise InspectorConfigurationError() from None
        if set(entries) != {self._candidate_receipt.name}:
            raise InspectorConfigurationError()
        stamp = _hash_regular_file(self._candidate_receipt, required=True)
        if stamp.size is None or stamp.size > _MAX_CANDIDATE_RECEIPT_BYTES:
            raise InspectorConfigurationError()
        try:
            raw = loads_strict(
                self._candidate_receipt.read_bytes(),
                max_bytes=_MAX_CANDIDATE_RECEIPT_BYTES,
            )
            expected = {
                "attempt_id",
                "candidate_state",
                "code_revision",
                "contract",
                "contract_version",
                "evidence",
                "evidence_sha256",
                "generated_at",
                "receipt_sha256",
            }
            if not isinstance(raw, dict) or set(raw) != expected:
                raise ValidationError("candidate receipt shape")
            receipt = Stage11CandidateReceipt(
                attempt_id=raw["attempt_id"],
                generated_at=raw["generated_at"],
                code_revision=raw["code_revision"],
                candidate_state=raw["candidate_state"],
                evidence=raw["evidence"],
                evidence_sha256=raw["evidence_sha256"],
                receipt_sha256=raw["receipt_sha256"],
            )
            evidence = Stage11CompletionEvidence.from_primitive(
                receipt.evidence
            )
        except (OSError, ResourceLimitError, TypeError, ValidationError):
            raise InspectorConfigurationError() from None
        expected_attempt = self._candidate_receipt.stem
        if (
            raw["contract"] != "quant_data.stage11_candidate_receipt"
            or raw["contract_version"] != "1.0.0"
            or receipt.attempt_id != expected_attempt
            or receipt.code_revision != self._registry_source_sha256
            or evidence.scope_manifest_sha256 != self._scope_manifest_sha256
            or evidence.target_profile_id != TARGET_PROFILE_ID
            or evidence.registry_source_sha256
            != self._registry_source_sha256
            or completion["evidence_sha256"] != receipt.evidence_sha256
            or completion["candidate_receipt_sha256"]
            != receipt.receipt_sha256
        ):
            raise InspectorConfigurationError()

    def _marker_state(self) -> dict[str, Any]:
        flags: dict[str, bool | None] = {
            "bea_published": None,
            "retail_published": None,
            "weekly_published": None,
        }
        if self._resume.exists():
            raw = self._read_marker(self._resume)
            expected_resume = {
                "bea_published",
                "contract",
                "registry_source_sha256",
                "retail_published",
                "scope_manifest_sha256",
                "target_profile_id",
                "version",
                "weekly_published",
            }
            if (
                set(raw) != expected_resume
                or raw.get("contract") != "quant_data.stage11_backfill_resume"
                or raw.get("version") != "1.0.0"
                or raw.get("target_profile_id") != TARGET_PROFILE_ID
                or raw.get("scope_manifest_sha256")
                != self._scope_manifest_sha256
                or raw.get("registry_source_sha256")
                != self._registry_source_sha256
            ):
                raise InspectorConfigurationError()
            for key in flags:
                value = raw.get(key)
                if not isinstance(value, bool):
                    raise InspectorConfigurationError()
                flags[key] = value

        completion_present = self._completion.exists()
        if completion_present:
            raw = self._read_marker(self._completion)
            expected_completion = {
                "candidate_receipt_sha256",
                "contract",
                "evidence_sha256",
                "registry_source_sha256",
                "scope_manifest_sha256",
                "sha256",
                "target_profile_id",
                "version",
            }
            if (
                set(raw) != expected_completion
                or raw.get("contract") != "quant_data.stage11_backfill_completion"
                or raw.get("version") != "1.0.0"
                or raw.get("target_profile_id") != TARGET_PROFILE_ID
                or raw.get("scope_manifest_sha256")
                != self._scope_manifest_sha256
                or raw.get("registry_source_sha256")
                != self._registry_source_sha256
            ):
                raise InspectorConfigurationError()
            digest_fields = (
                "candidate_receipt_sha256",
                "evidence_sha256",
                "registry_source_sha256",
                "scope_manifest_sha256",
                "sha256",
            )
            if any(
                not isinstance(raw.get(key), str)
                or _DIGEST.fullmatch(raw[key]) is None
                for key in digest_fields
            ):
                raise InspectorConfigurationError()
            material = {
                key: value for key, value in raw.items() if key != "sha256"
            }
            expected_sha = hashlib.sha256(
                dumps_strict(material).encode("utf-8")
            ).hexdigest()
            if raw["sha256"] != expected_sha:
                raise InspectorConfigurationError()
            if any(value is not True for value in flags.values()):
                raise InspectorConfigurationError()
            self._validate_candidate_receipt(raw)

        return {
            **flags,
            "completion_receipt_present": completion_present,
        }

    def summary(self) -> dict[str, Any]:
        def read(connection: sqlite3.Connection) -> dict[str, Any]:
            items: list[dict[str, Any]] = []
            for spec in SERIES:
                version_row = connection.execute(
                    f"""
                    SELECT COUNT(*) AS row_count,
                           MIN(period) AS first_period,
                           MAX(period) AS last_period
                    FROM "{spec.versions_relation}"
                    WHERE canonical_series_id=?
                    """,
                    (spec.canonical_id,),
                ).fetchone()
                current_count = int(
                    connection.execute(
                        f"""
                        SELECT COUNT(*)
                        FROM "{spec.current_relation}"
                        WHERE canonical_series_id=?
                        """,
                        (spec.canonical_id,),
                    ).fetchone()[0]
                )
                version_count = int(version_row["row_count"])
                items.append(
                    {
                        "slug": spec.slug,
                        "canonical_series_id": spec.canonical_id,
                        "label": spec.label,
                        "family": spec.family,
                        "state": "populated" if version_count else "not_yet_populated",
                        "current_count": current_count,
                        "version_count": version_count,
                        "first_period": version_row["first_period"],
                        "last_period": version_row["last_period"],
                    }
                )
            marker = self._marker_state()
            complete = bool(
                marker["completion_receipt_present"]
                and marker["bea_published"] is True
                and marker["retail_published"] is True
                and marker["weekly_published"] is True
                and all(item["version_count"] for item in items)
            )
            any_data = any(item["version_count"] for item in items)
            state = "complete" if complete else ("partial" if any_data else "empty")
            return {
                "contract": "quant_data.stage11_candidate_inspector_summary",
                "contract_version": "1.0.0",
                "execution": "read_only_immutable_snapshot",
                "target_profile_id": TARGET_PROFILE_ID,
                "candidate_state": state,
                "markers": marker,
                "series": items,
            }

        return self._guarded_read(read)

    def observations(self, query: Mapping[str, str]) -> dict[str, Any]:
        if "series" not in query or not set(query).issubset(
            {"series", "page", "limit", "direction"}
        ):
            raise InspectorValidationError()
        spec = _SERIES_BY_SLUG.get(query.get("series", ""))
        if spec is None:
            raise InspectorValidationError()
        page = _strict_int(query.get("page"), default=1, maximum=_PAGE_MAX)
        limit = _strict_int(query.get("limit"), default=25, maximum=_LIMIT_MAX)
        direction = query.get("direction", "desc")
        if direction not in {"asc", "desc"}:
            raise InspectorValidationError()
        sql_direction = "ASC" if direction == "asc" else "DESC"
        offset = (page - 1) * limit

        def read(connection: sqlite3.Connection) -> dict[str, Any]:
            total = int(
                connection.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM "{spec.versions_relation}"
                    WHERE canonical_series_id=?
                    """,
                    (spec.canonical_id,),
                ).fetchone()[0]
            )
            dimensions = ", ".join(f'"{item}"' for item in spec.dimensions)
            selected_dimensions = (", " + dimensions) if dimensions else ""
            rows = connection.execute(
                f"""
                SELECT period, value_text, unit,
                       available_at, available_precision,
                       captured_at, captured_precision,
                       correction_sequence, version_id, capture_id
                       {selected_dimensions}
                FROM "{spec.versions_relation}"
                WHERE canonical_series_id=?
                ORDER BY period {sql_direction},
                         correction_sequence {sql_direction}
                LIMIT ? OFFSET ?
                """,
                (spec.canonical_id, limit, offset),
            ).fetchall()
            rendered: list[dict[str, Any]] = []
            for row in rows:
                rendered.append(
                    {
                        "period": row["period"],
                        "value_text": row["value_text"],
                        "unit": row["unit"],
                        "available_at": row["available_at"],
                        "available_precision": row["available_precision"],
                        "captured_at": row["captured_at"],
                        "captured_precision": row["captured_precision"],
                        "correction_sequence": row["correction_sequence"],
                        "version_id": row["version_id"],
                        "capture_id": row["capture_id"],
                        "dimensions": {
                            name: row[name] for name in spec.dimensions
                        },
                    }
                )
            return {
                "contract": "quant_data.stage11_candidate_inspector_observations",
                "contract_version": "1.0.0",
                "execution": "read_only_immutable_snapshot",
                "target_profile_id": TARGET_PROFILE_ID,
                "series": {
                    "slug": spec.slug,
                    "canonical_series_id": spec.canonical_id,
                    "label": spec.label,
                    "family": spec.family,
                    "state": "populated" if total else "not_yet_populated",
                },
                "query": {
                    "page": page,
                    "limit": limit,
                    "direction": direction,
                },
                "pagination": {
                    "total_rows": total,
                    "returned_rows": len(rendered),
                    "has_previous": page > 1,
                    "has_next": offset + len(rendered) < total,
                },
                "rows": rendered,
            }

        return self._guarded_read(read)

    def handle(self, method: str, target: str) -> InspectorResponse:
        if method != "GET":
            raise InspectorMethodError()
        if not isinstance(target, str) or len(target) > _MAX_TARGET_CHARS:
            raise InspectorResourceError()
        try:
            split = urlsplit(target)
        except ValueError:
            raise InspectorValidationError() from None
        if split.scheme or split.netloc or split.fragment:
            raise InspectorValidationError()
        path = split.path
        if path == "/assets/dashboard.css":
            if split.query:
                raise InspectorValidationError()
            return _asset_response(_ASSET_ROOT / "dashboard.css", "text/css; charset=utf-8")
        if path == "/assets/inter-variable.woff2":
            if split.query:
                raise InspectorValidationError()
            return _asset_response(_ASSET_ROOT / "inter-variable.woff2", "font/woff2")
        if path in {"/", "/api/summary"}:
            _parse_query(split.query, allowed=frozenset())
            value = self.summary()
            if path == "/api/summary":
                return _json_response(HTTPStatus.OK, value)
            return _html_response(_render_summary(value))
        if path in {"/observations", "/api/observations"}:
            query = _parse_query(
                split.query,
                allowed=frozenset({"series", "page", "limit", "direction"}),
            )
            value = self.observations(query)
            if path == "/api/observations":
                return _json_response(HTTPStatus.OK, value)
            return _html_response(_render_observations(value))
        raise InspectorNotFoundError()


def _asset_response(path: Path, content_type: str) -> InspectorResponse:
    try:
        root = _ASSET_ROOT.resolve(strict=True)
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
        body = resolved.read_bytes()
    except (OSError, ValueError):
        raise InspectorConfigurationError() from None
    return InspectorResponse(HTTPStatus.OK, body, content_type)


def _json_response(status: int, value: Mapping[str, Any]) -> InspectorResponse:
    return InspectorResponse(
        int(status),
        dumps_strict(dict(value)).encode("utf-8"),
        "application/json; charset=utf-8",
    )


def _html_response(document: str) -> InspectorResponse:
    return InspectorResponse(
        HTTPStatus.OK,
        document.encode("utf-8"),
        "text/html; charset=utf-8",
    )


def _error_response(error: InspectorError) -> InspectorResponse:
    return _json_response(
        error.status,
        {
            "contract": "quant_data.stage11_candidate_inspector_error",
            "error": {
                "code": error.code,
                "message": error.public_message,
            },
        },
    )


def _text(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return html.escape(str(value), quote=True)


def _shell(*, title: str, eyebrow: str, body: str, state: str) -> str:
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{_text(title)} · Quant Data Infrastructure</title>"
        '<link rel="stylesheet" href="/assets/dashboard.css"></head><body>'
        '<a class="skip-link" href="#main-content">Skip to content</a>'
        '<div class="app-shell"><header class="site-header">'
        '<a class="brand" href="/"><span class="brand-mark">QD</span>'
        "<span><strong>Quant Data</strong> Infrastructure</span></a>"
        '<p class="shell-status"><span aria-hidden="true">●</span>'
        "Read-only Stage 11 candidate</p></header>"
        '<nav class="primary-nav" aria-label="Candidate inspector">'
        '<a href="/" aria-current="page">Candidate summary</a>'
        "</nav><main id=\"main-content\"><header class=\"page-intro\">"
        f'<p class="eyebrow">{_text(eyebrow)}</p><h1>{_text(title)}</h1>'
        '<p class="lede">Fixed, bounded views over one immutable candidate snapshot. '
        "No raw SQL, database path, provider call, or write capability is exposed.</p>"
        f'<p class="receipt-line">Candidate state: <strong>{_text(state)}</strong></p>'
        f"</header>{body}</main><footer class=\"site-footer\">"
        "<p>Local loopback inspection only · candidate data remains unpromoted</p>"
        "</footer></div></body></html>"
    )


def _render_summary(value: Mapping[str, Any]) -> str:
    markers = value.get("markers", {})
    marker_rows = "".join(
        f"<tr><th scope=\"row\">{_text(label)}</th><td>{_text(markers.get(key))}</td></tr>"
        for key, label in (
            ("bea_published", "BEA publication recorded"),
            ("retail_published", "EIA retail publication recorded"),
            ("weekly_published", "EIA weekly publication recorded"),
            ("completion_receipt_present", "Completion receipt present"),
        )
    )
    series_rows = "".join(
        "<tr><th scope=\"row\">"
        + _text(item.get("label"))
        + "</th><td><code>"
        + _text(item.get("canonical_series_id"))
        + "</code></td><td>"
        + _text(item.get("state"))
        + "</td><td>"
        + _text(item.get("current_count"))
        + "</td><td>"
        + _text(item.get("version_count"))
        + "</td><td>"
        + _text(item.get("first_period"))
        + "</td><td>"
        + _text(item.get("last_period"))
        + "</td><td><a href=\"/observations?"
        + html.escape(
            urlencode(
                {
                    "series": item.get("slug", ""),
                    "page": "1",
                    "limit": "100",
                    "direction": "desc",
                }
            ),
            quote=True,
        )
        + "\">Inspect</a></td></tr>"
        for item in value.get("series", [])
        if isinstance(item, Mapping)
    )
    body = (
        '<section class="panel"><div class="panel-heading"><p class="panel-kicker">Run state</p>'
        "<h2>Candidate receipts</h2></div><div class=\"table-scroll\"><table>"
        "<caption>Sanitized Stage 11 resume and completion state</caption><tbody>"
        + marker_rows
        + "</tbody></table></div></section>"
        '<section class="panel"><div class="panel-heading"><p class="panel-kicker">Stored history</p>'
        "<h2>Reviewed macro series</h2></div><div class=\"table-scroll\" tabindex=\"0\">"
        "<table><caption>Current and versioned rows in the candidate macro store</caption>"
        "<thead><tr><th>Series</th><th>Canonical ID</th><th>State</th>"
        "<th>Current</th><th>Versions</th><th>First</th><th>Last</th><th></th>"
        "</tr></thead><tbody>"
        + series_rows
        + "</tbody></table></div></section>"
    )
    return _shell(
        title="Stage 11 candidate inspector",
        eyebrow="BEA and EIA history",
        body=body,
        state=str(value.get("candidate_state", "unknown")),
    )


def _observation_url(
    *, slug: str, page: int, limit: int, direction: str
) -> str:
    return "/observations?" + urlencode(
        {
            "series": slug,
            "page": str(page),
            "limit": str(limit),
            "direction": direction,
        }
    )


def _render_observations(value: Mapping[str, Any]) -> str:
    series = value.get("series", {})
    query = value.get("query", {})
    pagination = value.get("pagination", {})
    if not isinstance(series, Mapping) or not isinstance(query, Mapping):
        raise InspectorConfigurationError()
    slug = str(series.get("slug", ""))
    page = int(query.get("page", 1))
    limit = int(query.get("limit", 25))
    direction = str(query.get("direction", "desc"))
    options = "".join(
        '<option value="'
        + _text(item.slug)
        + ('" selected>' if item.slug == slug else '">')
        + _text(item.label)
        + "</option>"
        for item in SERIES
    )
    rows = "".join(
        "<tr><td>"
        + _text(row.get("period"))
        + "</td><td>"
        + _text(row.get("value_text"))
        + "</td><td>"
        + _text(row.get("unit"))
        + "</td><td>"
        + _text(row.get("available_at"))
        + "</td><td>"
        + _text(row.get("captured_at"))
        + "</td><td>"
        + _text(row.get("correction_sequence"))
        + "</td><td><code>"
        + _text(row.get("dimensions"))
        + "</code></td><td><code>"
        + _text(row.get("version_id"))
        + "</code></td><td><code>"
        + _text(row.get("capture_id"))
        + "</code></td></tr>"
        for row in value.get("rows", [])
        if isinstance(row, Mapping)
    )
    if not rows:
        rows = '<tr><td colspan="9">This authorized series is not yet populated.</td></tr>'
    links: list[str] = []
    if pagination.get("has_previous"):
        links.append(
            '<a href="'
            + _text(
                _observation_url(
                    slug=slug,
                    page=page - 1,
                    limit=limit,
                    direction=direction,
                )
            )
            + '">Previous page</a>'
        )
    if pagination.get("has_next"):
        links.append(
            '<a href="'
            + _text(
                _observation_url(
                    slug=slug,
                    page=page + 1,
                    limit=limit,
                    direction=direction,
                )
            )
            + '">Next page</a>'
        )
    body = (
        '<section class="panel"><div class="panel-heading"><p class="panel-kicker">Bounded query</p>'
        "<h2>Choose a reviewed series</h2></div>"
        '<form class="query-form" action="/observations" method="get"><fieldset>'
        '<legend>Versioned observation query</legend><div class="form-grid">'
        '<label>Series<select name="series" required>'
        + options
        + "</select></label>"
        '<label>Page<input name="page" type="number" min="1" max="100" value="'
        + _text(page)
        + '"></label><label>Rows<input name="limit" type="number" min="1" max="100" value="'
        + _text(limit)
        + '"></label><label>Direction<select name="direction">'
        + ('<option value="desc" selected>Newest first</option>' if direction == "desc" else '<option value="desc">Newest first</option>')
        + ('<option value="asc" selected>Oldest first</option>' if direction == "asc" else '<option value="asc">Oldest first</option>')
        + "</select></label></div><div class=\"form-actions\"><button type=\"submit\">Inspect rows</button>"
        '<p class="form-bound">Only fixed Stage 11 series and server-owned queries are available.</p>'
        "</div></fieldset></form></section>"
        '<section class="panel"><div class="panel-heading"><p class="panel-kicker">Versioned rows</p><h2>'
        + _text(series.get("label"))
        + "</h2><p>"
        + _text(series.get("family"))
        + " · "
        + _text(pagination.get("total_rows"))
        + " total versions</p></div><div class=\"table-scroll\" tabindex=\"0\"><table>"
        "<caption>Immutable provider-backed observation versions</caption><thead><tr>"
        "<th>Period</th><th>Value</th><th>Unit</th><th>Available</th><th>Captured</th>"
        "<th>Correction</th><th>Dimensions</th><th>Version ID</th><th>Capture ID</th>"
        "</tr></thead><tbody>"
        + rows
        + "</tbody></table></div><nav class=\"pagination\" aria-label=\"Observation pages\">"
        + " · ".join(links)
        + "</nav></section>"
    )
    return _shell(
        title=str(series.get("label", "Stage 11 observations")),
        eyebrow="Versioned macro observations",
        body=body,
        state=str(series.get("state", "unknown")),
    )


def create_server(
    application: Stage11CandidateInspector,
    *,
    host: str = LOOPBACK_HOST,
    port: int = 0,
) -> ThreadingHTTPServer:
    if not isinstance(application, Stage11CandidateInspector):
        raise InspectorConfigurationError()
    if host != LOOPBACK_HOST:
        raise InspectorConfigurationError()
    if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
        raise InspectorConfigurationError()

    class Handler(BaseHTTPRequestHandler):
        server_version = "QuantDataStage11Inspector/1.0"
        sys_version = ""
        protocol_version = "HTTP/1.1"

        def __getattr__(self, name: str) -> Any:
            if name.startswith("do_"):
                return self._method_not_allowed
            raise AttributeError(name)

        def do_GET(self) -> None:
            self._serve()

        def _method_not_allowed(self) -> None:
            self._write(_error_response(InspectorMethodError()))

        def _serve(self) -> None:
            try:
                if self.headers.get("Transfer-Encoding"):
                    raise InspectorValidationError()
                content_length = self.headers.get("Content-Length")
                if content_length not in {None, "0"}:
                    raise InspectorValidationError()
                response = application.handle("GET", self.path)
            except InspectorError as error:
                response = _error_response(error)
            except Exception:
                response = _error_response(InspectorError())
            self._write(response)

        def _write(self, response: InspectorResponse) -> None:
            self.send_response(int(response.status))
            for key, value in _SECURITY_HEADERS.items():
                self.send_header(key, value)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            self.end_headers()
            if self.command != "HEAD":
                try:
                    self.wfile.write(response.body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        def send_error(
            self,
            code: int,
            message: str | None = None,
            explain: str | None = None,
        ) -> None:
            del message, explain
            if code == HTTPStatus.REQUEST_URI_TOO_LONG:
                response = _error_response(InspectorResourceError())
            elif 400 <= code < 500:
                response = _error_response(InspectorValidationError())
            else:
                response = _error_response(InspectorError())
            self.close_connection = True
            self._write(response)

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    try:
        server = ThreadingHTTPServer((host, port), Handler)
    except OSError:
        raise InspectorConfigurationError() from None
    server.daemon_threads = True
    return server


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m quant_data.candidate_inspector",
        description="Serve the fixed read-only Stage 11 candidate inspector.",
        allow_abbrev=False,
    )
    parser.add_argument("--port", type=int, default=0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if isinstance(arguments.port, bool) or not 0 <= arguments.port <= 65535:
        print(
            dumps_strict(
                {
                    "contract": "quant_data.stage11_candidate_inspector_startup_error",
                    "error": {"code": "invalid_port", "message": "Port is invalid."},
                }
            ),
            file=sys.stderr,
            flush=True,
        )
        return 2
    try:
        application = Stage11CandidateInspector()
        server = create_server(application, port=arguments.port)
    except InspectorError as error:
        print(
            dumps_strict(
                {
                    "contract": "quant_data.stage11_candidate_inspector_startup_error",
                    "error": {"code": error.code, "message": error.public_message},
                }
            ),
            file=sys.stderr,
            flush=True,
        )
        return 1
    print(
        dumps_strict(
            {
                "contract": "quant_data.stage11_candidate_inspector_ready",
                "read_only": True,
                "status": "ready",
                "target_profile_id": TARGET_PROFILE_ID,
                "url": f"http://{LOOPBACK_HOST}:{server.server_address[1]}/",
            }
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "APPROVED_TARGET_ROOT",
    "LOOPBACK_HOST",
    "SERIES",
    "Stage11CandidateInspector",
    "create_server",
    "main",
)
