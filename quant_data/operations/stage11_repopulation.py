"""Read-only Stage 11 BEA/EIA reconciliation and private candidate evidence.

This module is intentionally downstream of the bounded Stage 11 capture and
publication layers.  It has no transport, credential lookup, default store
selection, scheduler, or operational-promotion path.  It only reads an
explicit four-store map, reparses the retained *sanitized* raw evidence, and
proves that the dedicated Stage 11 canonical relations are a faithful current
projection of the immutable correction chains.

The private receipt helper is deliberately receipt-only.  Its output says that
an explicit candidate passed offline reconciliation; it cannot select or
promote an operational store.
"""

from __future__ import annotations

import hashlib
import itertools
import os
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping

from ..errors import ConflictError, ResourceLimitError, ValidationError
from ..fingerprint import logical_manifest, mutation_fingerprint
from ..json_codec import dumps_strict, loads_strict
from ..macro.stage11_bea import (
    BEA_NIPA_TABLE_SERIES,
    BeaNipaCapture,
    BeaNipaHistoryCohort,
    assemble_bea_nipa_history,
    parse_bea_nipa_response,
    prepare_bea_nipa_capture,
)
from ..macro.stage11_eia import (
    EIA_RETAIL_CANONICAL_SERIES_BY_METRIC,
    EIA_RETAIL_METRICS,
    EIA_RETAIL_SECTOR_ID,
    EIA_RETAIL_STATE_ID,
    EIA_WEEKLY_CANONICAL_SERIES_ID,
    EIA_WEEKLY_SERIES_ID,
    EiaRetailCapture,
    EiaRetailPageCapture,
    EiaWeeklyCapture,
    assemble_eia_retail_capture,
    parse_eia_retail_page_response,
    parse_eia_weekly_response,
    prepare_eia_retail_page,
    prepare_eia_weekly_capture,
)
from ..macro.stage11_publication import (
    BEA_NIPA_HISTORY_COLLECTOR_ID,
    BEA_NIPA_HISTORY_DATASET_ID,
    BEA_NIPA_HISTORY_EVIDENCE_DATASET_ID,
    BEA_NIPA_NORMALIZATION_VERSION,
    BEA_NIPA_RELATIONS,
    EIA_ELECTRICITY_RETAIL_HISTORY_COLLECTOR_ID,
    EIA_ELECTRICITY_RETAIL_HISTORY_DATASET_ID,
    EIA_ELECTRICITY_RETAIL_HISTORY_EVIDENCE_DATASET_ID,
    EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_COLLECTOR_ID,
    EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_DATASET_ID,
    EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_EVIDENCE_DATASET_ID,
    EIA_RETAIL_NORMALIZATION_VERSION,
    EIA_RETAIL_RELATIONS,
    EIA_WEEKLY_NORMALIZATION_VERSION,
    EIA_WEEKLY_RELATIONS,
    STAGE11_MIGRATION_ID,
    STAGE11_MIGRATION_RESOURCE,
    STAGE11_MIGRATION_SHA256,
    Stage11MacroImporter,
)
from ..macro.stage11_scope import (
    REVIEWED_STAGE11_MACRO_SCOPE_SHA256,
    STAGE11_REQUIRED_MARKET_CANDIDATE_CONTRACT,
    STAGE11_REQUIRED_MARKET_PROFILE_ID,
    STAGE11_SCOPE_CONTRACT,
    STAGE11_SCOPE_VERSION,
    STAGE11_TARGET_PROFILE_ID,
    STAGE11_TARGET_ROOT,
    Stage11MacroScope,
)
from ..registry import Registry
from ..stores import StoreMap, StoreRole, canonical_path_uri, read_connection, stable_id
from ..temporal import TemporalPrecision, TemporalValue
from .health import all_store_health


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CODE_REVISION = re.compile(r"^[0-9a-f]{7,64}$")
_ATTEMPT_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,159}$")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_SCOPE_BYTES = 128 * 1024
_MAX_RECEIPT_BYTES = 512 * 1024
_CANDIDATE_STATE = "private_candidate_only_no_operational_promotion"
_REPLAY_CLOCK = datetime(1970, 1, 1, tzinfo=timezone.utc)

# Kept in step with the two capture parsers and the publication defence in
# depth check.  A retained raw body may never become a covert request log.
_FORBIDDEN_EVIDENCE_TOKENS = (
    b"api_key",
    b"apikey",
    b"user_id",
    b"userid",
    b"authorization",
    b"authentication",
    b"credential",
    b"bearer",
    b"cookie",
    b"http://",
    b"https://",
    b'"headers"',
    b'"header"',
    b'"query"',
)
_FORBIDDEN_PUBLIC_TOKENS = (
    "api_key",
    "apikey",
    "user_id",
    "userid",
    "authorization",
    "authentication",
    "credential",
    "bearer",
    "cookie",
    "http://",
    "https://",
    "file:",
)

_BEA_OUTPUT_DATASETS = (
    BEA_NIPA_HISTORY_EVIDENCE_DATASET_ID,
    BEA_NIPA_HISTORY_DATASET_ID,
)
_EIA_RETAIL_OUTPUT_DATASETS = (
    EIA_ELECTRICITY_RETAIL_HISTORY_EVIDENCE_DATASET_ID,
    EIA_ELECTRICITY_RETAIL_HISTORY_DATASET_ID,
)
_EIA_WEEKLY_OUTPUT_DATASETS = (
    EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_EVIDENCE_DATASET_ID,
    EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_DATASET_ID,
)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: object) -> str:
    return _sha256_bytes(dumps_strict(value).encode("utf-8"))


def _require_digest(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValidationError(f"Stage 11 {field_name} must be a lowercase SHA-256 digest")
    return value


def _require_bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValidationError(f"Stage 11 {field_name} must be boolean")
    return value


def _require_int(value: object, field_name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValidationError(f"Stage 11 {field_name} must be an integer in range")
    return value


def _require_text(value: object, field_name: str, *, maximum: int = 512) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValidationError(f"Stage 11 {field_name} must be bounded normalized text")
    return value


def _canonical_datetime(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"Stage 11 {field_name} must be an aware datetime")
    parsed = TemporalValue.parse(value, pointer=f"/{field_name}")
    if (
        parsed.precision is not TemporalPrecision.DATETIME
        or not isinstance(parsed.value, datetime)
    ):
        raise ValidationError(f"Stage 11 {field_name} must be an aware datetime")
    normalized = (
        parsed.value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )
    if value != normalized:
        raise ValidationError(f"Stage 11 {field_name} must use canonical UTC text")
    return normalized


def _decimal_text(value: object, field_name: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValidationError(f"Stage 11 {field_name} must be a finite decimal")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError(f"Stage 11 {field_name} must be a finite decimal") from exc
    if not parsed.is_finite():
        raise ValidationError(f"Stage 11 {field_name} must be a finite decimal")
    return "0" if parsed.is_zero() else format(parsed.normalize(), "f")


def _freeze_json(value: object, field_name: str) -> object:
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValidationError(f"Stage 11 {field_name} keys must be strings")
            frozen[key] = _freeze_json(child, field_name)
        return MappingProxyType(frozen)
    if isinstance(value, (tuple, list)):
        return tuple(_freeze_json(child, field_name) for child in value)
    if isinstance(value, (str, int, bool, type(None))):
        return value
    raise ValidationError(f"Stage 11 {field_name} must be strict JSON material")


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(child) for child in value]
    return value


def _strict_json_object(value: object, field_name: str, *, maximum: int = _MAX_SCOPE_BYTES) -> dict[str, object]:
    if not isinstance(value, str):
        raise ValidationError(f"Stage 11 {field_name} must be strict JSON")
    try:
        parsed = loads_strict(value, max_bytes=maximum)
    except (ResourceLimitError, ValidationError):
        raise ValidationError(f"Stage 11 {field_name} must be strict JSON") from None
    if not isinstance(parsed, Mapping) or not all(isinstance(key, str) for key in parsed):
        raise ValidationError(f"Stage 11 {field_name} must be a JSON object")
    rendered = dumps_strict(dict(parsed), max_bytes=maximum)
    if rendered != value:
        raise ValidationError(f"Stage 11 {field_name} must be canonical strict JSON")
    return dict(parsed)


def _assert_json_column(value: object, expected: Mapping[str, object], field_name: str) -> None:
    parsed = _strict_json_object(value, field_name)
    rendered = dumps_strict(dict(expected))
    if parsed != dict(expected) or value != rendered:
        raise ValidationError(f"Stage 11 {field_name} differs from its reviewed scope")


def _assert_sanitized_raw(value: object, field_name: str, *, maximum: int) -> bytes:
    if not isinstance(value, bytes) or not value or len(value) > maximum:
        raise ValidationError(f"Stage 11 {field_name} retained bytes are invalid")
    lowered = value.lower()
    if any(token in lowered for token in _FORBIDDEN_EVIDENCE_TOKENS):
        raise ValidationError(f"Stage 11 {field_name} contains request or credential material")
    try:
        loads_strict(value, max_bytes=maximum)
    except (ResourceLimitError, ValidationError):
        raise ValidationError(f"Stage 11 {field_name} must be strict sanitized JSON") from None
    return value


def _assert_public_safe(value: object, field_name: str) -> None:
    """Keep candidate evidence and receipts free of paths and secret material."""

    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValidationError(f"Stage 11 {field_name} keys must be strings")
            normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
            if (
                any(token in normalized for token in ("apikey", "userid", "authorization", "credential", "header", "cookie", "password", "token"))
                or normalized.endswith("path")
                or normalized in {"root", "url", "uri", "location"}
            ):
                raise ValidationError(f"Stage 11 {field_name} contains an unsafe public key")
            _assert_public_safe(child, field_name)
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            _assert_public_safe(child, field_name)
        return
    if isinstance(value, str):
        lowered = value.casefold()
        if (
            any(token in lowered for token in _FORBIDDEN_PUBLIC_TOKENS)
            or value.startswith("/")
            or value.startswith("~")
            or "\\" in value
        ):
            raise ValidationError(f"Stage 11 {field_name} contains a secret or physical path")
        return
    if not isinstance(value, (int, bool, type(None))):
        raise ValidationError(f"Stage 11 {field_name} must be strict JSON material")


def _validate_scope(scope: object) -> Stage11MacroScope:
    if not isinstance(scope, Stage11MacroScope):
        raise ValidationError("Stage 11 reconciliation requires a validated immutable scope")
    manifest = scope.manifest_mapping()
    if (
        scope.manifest_sha256 != REVIEWED_STAGE11_MACRO_SCOPE_SHA256
        or _sha256_json(manifest) != REVIEWED_STAGE11_MACRO_SCOPE_SHA256
        or scope.contract != STAGE11_SCOPE_CONTRACT
        or scope.version != STAGE11_SCOPE_VERSION
        or scope.target_profile_id != STAGE11_TARGET_PROFILE_ID
        or scope.target_root != STAGE11_TARGET_ROOT
        or scope.dependency.required_market_candidate_contract
        != STAGE11_REQUIRED_MARKET_CANDIDATE_CONTRACT
        or scope.dependency.required_market_profile_id
        != STAGE11_REQUIRED_MARKET_PROFILE_ID
        or tuple((request.table_name, request.series[0].provider_series_code) for request in scope.bea.requests)
        != BEA_NIPA_TABLE_SERIES
        or tuple(item.canonical_series_id for request in scope.bea.requests for item in request.series)
        != ("macro.gdp.real_qoq_saar_pct", "macro.gdp.nominal_billions")
        or scope.eia.retail.path != "/v2/electricity/retail-sales/data"
        or scope.eia.retail.frequency != "monthly"
        or scope.eia.retail.length != 5_000
        or dict(scope.eia.retail.facets) != {"sectorid": ("ALL",), "stateid": ("US",)}
        or tuple(item.field for item in scope.eia.retail.data) != EIA_RETAIL_METRICS
        or tuple(item.canonical_series_id for item in scope.eia.retail.data)
        != tuple(EIA_RETAIL_CANONICAL_SERIES_BY_METRIC[item] for item in EIA_RETAIL_METRICS)
        or scope.eia.weekly.path != "/v2/seriesid/PET.WCESTUS1.W"
        or scope.eia.weekly.provider_series_id != EIA_WEEKLY_SERIES_ID
        or scope.eia.weekly.canonical_series_id != EIA_WEEKLY_CANONICAL_SERIES_ID
    ):
        raise ValidationError("Stage 11 scope is not the reviewed immutable profile")
    return scope


def _collector_by_id(registry: Registry, collector_id: str) -> Mapping[str, Any]:
    matches = [item for item in registry.collectors if item.get("id") == collector_id]
    if len(matches) != 1:
        raise ValidationError("Stage 11 collector is not registered exactly once")
    return matches[0]


def _validate_registry(registry: object) -> Registry:
    """Validate Stage 11 declarations independently of publication code."""

    if (
        not isinstance(registry, Registry)
        or registry.schema_version != "1.7.0"
        or registry.registry_version != "2.9.0"
        or registry.status != "validated"
    ):
        raise ValidationError("Stage 11 reconciliation requires the reviewed 1.7.0/2.9.0 registry")
    migration_matches = [item for item in registry.migrations if item.id == STAGE11_MIGRATION_ID]
    if len(migration_matches) != 1:
        raise ValidationError("Stage 11 migration is not registered exactly once")
    migration = migration_matches[0]
    if (
        migration.store != StoreRole.MACRO.value
        or migration.ordinal != 12
        or migration.resource != STAGE11_MIGRATION_RESOURCE
        or migration.sha256 != STAGE11_MIGRATION_SHA256
        or migration.reconstruction_state != "fixture_validated"
        or registry.store(StoreRole.MACRO.value).migration_order[-1] != STAGE11_MIGRATION_ID
    ):
        raise ValidationError("Stage 11 migration declaration is invalid")

    expected_datasets = {
        BEA_NIPA_HISTORY_EVIDENCE_DATASET_ID: ("evidence", BEA_NIPA_RELATIONS[:1], (BEA_NIPA_HISTORY_COLLECTOR_ID,)),
        BEA_NIPA_HISTORY_DATASET_ID: ("canonical", BEA_NIPA_RELATIONS[1:], (BEA_NIPA_HISTORY_COLLECTOR_ID,)),
        EIA_ELECTRICITY_RETAIL_HISTORY_EVIDENCE_DATASET_ID: ("evidence", EIA_RETAIL_RELATIONS[:1], (EIA_ELECTRICITY_RETAIL_HISTORY_COLLECTOR_ID,)),
        EIA_ELECTRICITY_RETAIL_HISTORY_DATASET_ID: ("canonical", EIA_RETAIL_RELATIONS[1:], (EIA_ELECTRICITY_RETAIL_HISTORY_COLLECTOR_ID,)),
        EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_EVIDENCE_DATASET_ID: ("evidence", EIA_WEEKLY_RELATIONS[:1], (EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_COLLECTOR_ID,)),
        EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_DATASET_ID: ("canonical", EIA_WEEKLY_RELATIONS[1:], (EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_COLLECTOR_ID,)),
    }
    datasets = {item.id: item for item in registry.datasets}
    for dataset_id, (layer, relations, collectors) in expected_datasets.items():
        item = datasets.get(dataset_id)
        if (
            item is None
            or item.store != StoreRole.MACRO.value
            or item.layer != layer
            or not item.active
            or tuple(item.relations) != tuple(relations)
            or tuple(item.collector_ids) != collectors
            or item.tool_ids
            or item.dashboard_ids
            or item.export_ids
        ):
            raise ValidationError("Stage 11 dataset declaration is invalid")

    expected_collectors = (
        (
            BEA_NIPA_HISTORY_COLLECTOR_ID,
            "macro.stage11_bea_nipa_history",
            ("BEA_API_KEY",),
            _BEA_OUTPUT_DATASETS,
            {"max_bytes": 8_388_608, "max_requests": 2, "max_rows": 2_000, "max_seconds": 90},
        ),
        (
            EIA_ELECTRICITY_RETAIL_HISTORY_COLLECTOR_ID,
            "macro.stage11_eia_retail_history",
            ("EIA_API_KEY",),
            _EIA_RETAIL_OUTPUT_DATASETS,
            {"max_bytes": 16_777_216, "max_requests": 8, "max_rows": 40_000, "max_seconds": 360},
        ),
        (
            EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_COLLECTOR_ID,
            "macro.stage11_eia_weekly_history",
            ("EIA_API_KEY",),
            _EIA_WEEKLY_OUTPUT_DATASETS,
            {"max_bytes": 16_777_216, "max_requests": 1, "max_rows": 5_000, "max_seconds": 45},
        ),
    )
    for collector_id, handler, environment, outputs, bounds in expected_collectors:
        item = _collector_by_id(registry, collector_id)
        if (
            item.get("handler") != handler
            or tuple(item.get("configuration_env", ())) != environment
            or item.get("input_datasets") != []
            or item.get("network") is not True
            or tuple(item.get("output_datasets", ())) != outputs
            or item.get("schedule_eligibility") != {"mode": "manual_only"}
            or item.get("mutation_policy")
            != {"mode": "append_versions_and_move_current_projection", "unchanged": "zero_persistent_writes"}
            or item.get("workload_bounds") != bounds
        ):
            raise ValidationError("Stage 11 collector declaration is invalid")
    return registry


def _maps_are_disjoint(*store_maps: StoreMap) -> bool:
    seen: set[str] = set()
    for store_map in store_maps:
        identities = {canonical_path_uri(path) for _, path in store_map.items()}
        if seen.intersection(identities):
            return False
        seen.update(identities)
    return True


def _row_text(row: sqlite3.Row, field_name: str) -> str:
    return _require_text(row[field_name], field_name, maximum=1_024)


def _row_bytes(
    row: sqlite3.Row,
    column: str,
    field_name: str,
    *,
    maximum: int,
) -> bytes:
    value = row[column]
    return _assert_sanitized_raw(value, field_name, maximum=maximum)


def _assert_scope_digest(scope: Mapping[str, object], digest: object, field_name: str) -> None:
    _require_digest(digest, field_name)
    if digest != _sha256_json(dict(scope)):
        raise ValidationError(f"Stage 11 {field_name} differs from its exact request scope")


def _assert_capture_fields(
    row: sqlite3.Row,
    *,
    provider: str,
    endpoint_path: str,
    dataset_id: str,
    response_bytes: bytes,
    response_sha256: str,
    row_count: int,
    normalization_version: str,
    request_scope: Mapping[str, object],
) -> None:
    if (
        row["dataset_id"] != dataset_id
        or row["endpoint_path"] != endpoint_path
        or row["provider"] != provider
        or row["response_sha256"] != response_sha256
        or row["response_bytes"] != response_bytes
        or row["completeness"] != "complete"
        or row["availability_basis"] != "local_capture"
        or row["captured_precision"] != "datetime"
        or row["normalization_version"] != normalization_version
        or row["row_count"] != row_count
    ):
        raise ValidationError("Stage 11 retained capture fields differ from parsed evidence")
    _canonical_datetime(row["captured_at"], "capture captured_at")
    _assert_json_column(row["request_scope_json"], request_scope, "capture request_scope_json")
    _assert_scope_digest(request_scope, row["request_scope_sha256"], "capture request_scope_sha256")


def _assert_artifact(
    connection: sqlite3.Connection,
    *,
    artifact_id: str,
    run_id: str,
    dataset_id: str,
    content_sha256: str,
    byte_count: int,
    request_scope: Mapping[str, object],
    captured_at: str,
    normalization_version: str,
    source_reference: str,
) -> None:
    record = connection.execute(
        """
        SELECT artifact_id, run_id, dataset_id, content_sha256, media_type, byte_count,
               source_reference, request_scope_json, captured_at, captured_precision,
               normalization_version
        FROM ingestion_artifacts WHERE artifact_id=?
        """,
        (artifact_id,),
    ).fetchone()
    if record is None or (
        record["run_id"] != run_id
        or record["dataset_id"] != dataset_id
        or record["content_sha256"] != content_sha256
        or record["media_type"] != "application/json"
        or record["byte_count"] != byte_count
        or record["source_reference"] != source_reference
        or record["captured_at"] != captured_at
        or record["captured_precision"] != "datetime"
        or record["normalization_version"] != normalization_version
    ):
        raise ValidationError("Stage 11 artifact lineage differs from retained capture evidence")
    _assert_json_column(record["request_scope_json"], request_scope, "artifact request_scope_json")


def _assert_run_and_snapshot(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    snapshot_id: str,
    dataset_id: str,
    semantic_identity: str,
    command: str,
    request_scope: Mapping[str, object],
    captured_at: str,
    fetched_count: int,
    output_datasets: tuple[str, str],
    artifact_ids: tuple[str, ...],
) -> None:
    run = connection.execute(
        """
        SELECT run_id, dataset_id, semantic_identity, command, scope_json, status,
               started_at, completed_at, snapshot_id, fetched_count
        FROM ingestion_runs WHERE run_id=?
        """,
        (run_id,),
    ).fetchone()
    if run is None or (
        run["dataset_id"] != dataset_id
        or run["semantic_identity"] != semantic_identity
        or run["command"] != command
        or run["status"] != "succeeded"
        or run["started_at"] != captured_at
        or run["completed_at"] != captured_at
        or run["snapshot_id"] != snapshot_id
        or run["fetched_count"] != fetched_count
    ):
        raise ValidationError("Stage 11 ingestion run differs from complete parsed history")
    _assert_json_column(run["scope_json"], request_scope, "run scope_json")
    outputs = tuple(
        row["dataset_id"]
        for row in connection.execute(
            "SELECT dataset_id FROM ingestion_run_outputs WHERE run_id=? ORDER BY dataset_id",
            (run_id,),
        )
    )
    if outputs != tuple(sorted(output_datasets)):
        raise ValidationError("Stage 11 ingestion run outputs are incomplete")
    snapshot = connection.execute(
        """
        SELECT snapshot_id, run_id, dataset_id, semantic_identity, scope_json,
               completeness, row_count, captured_at, captured_precision, validation_state
        FROM ingestion_snapshots WHERE snapshot_id=?
        """,
        (snapshot_id,),
    ).fetchone()
    if snapshot is None or (
        snapshot["run_id"] != run_id
        or snapshot["dataset_id"] != dataset_id
        or snapshot["semantic_identity"] != semantic_identity
        or snapshot["completeness"] != "complete"
        or snapshot["row_count"] != fetched_count
        or snapshot["captured_at"] != captured_at
        or snapshot["captured_precision"] != "datetime"
        or snapshot["validation_state"] != "validated"
    ):
        raise ValidationError("Stage 11 ingestion snapshot differs from complete parsed history")
    _assert_json_column(snapshot["scope_json"], request_scope, "snapshot scope_json")
    snapshot_artifacts = tuple(
        row["artifact_id"]
        for row in connection.execute(
            """
            SELECT artifact_id FROM ingestion_snapshot_artifacts
            WHERE snapshot_id=? ORDER BY artifact_ordinal
            """,
            (snapshot_id,),
        )
    )
    if snapshot_artifacts != artifact_ids:
        raise ValidationError("Stage 11 snapshot artifact lineage is incomplete")


@dataclass(frozen=True, slots=True)
class _BeaStoredCapture:
    capture_id: str
    artifact_id: str
    snapshot_id: str
    run_id: str
    captured_at: str
    raw: BeaNipaCapture


@dataclass(frozen=True, slots=True)
class _RetailStoredPage:
    capture_id: str
    artifact_id: str
    snapshot_id: str
    run_id: str
    captured_at: str
    raw: EiaRetailPageCapture


@dataclass(frozen=True, slots=True)
class _WeeklyStoredCapture:
    capture_id: str
    artifact_id: str
    snapshot_id: str
    run_id: str
    captured_at: str
    raw: EiaWeeklyCapture


@dataclass(frozen=True, slots=True)
class _BeaRun:
    run_id: str
    captured_at: str
    cohort: BeaNipaHistoryCohort
    captures: tuple[_BeaStoredCapture, ...]
    semantic_identity: str


@dataclass(frozen=True, slots=True)
class _RetailRun:
    run_id: str
    captured_at: str
    cohort: EiaRetailCapture
    pages: tuple[_RetailStoredPage, ...]
    semantic_identity: str


@dataclass(frozen=True, slots=True)
class _WeeklyRun:
    run_id: str
    captured_at: str
    capture: EiaWeeklyCapture
    stored: _WeeklyStoredCapture
    semantic_identity: str


@dataclass(frozen=True, slots=True)
class _ReconciledMaterial:
    public: "Stage11MacroReconciliation"
    bea_runs: tuple[_BeaRun, ...]
    retail_runs: tuple[_RetailRun, ...]
    weekly_runs: tuple[_WeeklyRun, ...]


def _capture_semantic_identity(
    publication_semantic_identity: str,
    request_scope: Mapping[str, object],
    response_sha256: str,
    normalized_sha256: str,
    *,
    normalized_key: str,
) -> str:
    return _sha256_json(
        {
            "publication_semantic_identity": publication_semantic_identity,
            "request_scope": dict(request_scope),
            "response_sha256": response_sha256,
            normalized_key: normalized_sha256,
        }
    )


def _publication_identity(
    *,
    collector_id: str,
    dataset_id: str,
    request_scope: Mapping[str, object],
    normalization_version: str,
    normalized_sha256: str,
) -> str:
    return _sha256_json(
        {
            "collector_id": collector_id,
            "canonical_dataset_id": dataset_id,
            "request_scope": dict(request_scope),
            "normalization_version": normalization_version,
            "normalized_complete_history_sha256": normalized_sha256,
        }
    )


def _require_expected_id(actual: object, expected: str, field_name: str) -> None:
    if not isinstance(actual, str) or actual != expected:
        raise ValidationError(f"Stage 11 {field_name} is not derived from reviewed evidence")


def _load_bea_runs(connection: sqlite3.Connection) -> tuple[_BeaRun, ...]:
    rows = tuple(
        connection.execute(
            """
            SELECT capture_id, dataset_id, provider, endpoint_path, table_name, series_code,
                   request_scope_json, request_scope_sha256, response_sha256,
                   response_bytes, semantic_identity, completeness, availability_basis,
                   artifact_id, snapshot_id, captured_at, captured_precision, row_count,
                   normalization_version, run_id
            FROM stage11_bea_nipa_captures
            ORDER BY captured_at, run_id, table_name, series_code, capture_id
            """
        )
    )
    if not rows:
        raise ValidationError("Stage 11 BEA evidence is missing")
    run_rows = {
        _row_text(row, "run_id"): row
        for row in connection.execute(
            """
            SELECT run_id, dataset_id, semantic_identity, command, scope_json, status,
                   started_at, completed_at, snapshot_id, fetched_count
            FROM ingestion_runs
            WHERE dataset_id=? AND status='succeeded'
            ORDER BY started_at, run_id
            """,
            (BEA_NIPA_HISTORY_DATASET_ID,),
        )
    }
    if not run_rows:
        raise ValidationError("Stage 11 BEA publication runs are missing")
    captures: list[_BeaStoredCapture] = []
    record_by_capture_id: dict[str, sqlite3.Row] = {}
    for row in rows:
        table_name = _row_text(row, "table_name")
        series_code = _row_text(row, "series_code")
        prepared = prepare_bea_nipa_capture(table_name)
        if prepared.series_code != series_code:
            raise ValidationError("Stage 11 BEA capture has an unapproved table/series pair")
        response_bytes = _row_bytes(row, "response_bytes", "BEA response_bytes", maximum=8_388_608)
        parsed = parse_bea_nipa_response(body=response_bytes, prepared=prepared)
        if parsed.raw_bytes != response_bytes or parsed.raw_bytes_sha256 != row["response_sha256"]:
            raise ValidationError("Stage 11 BEA raw response differs after reparsing")
        request_scope = parsed.request_scope
        _assert_capture_fields(
            row,
            provider="bea",
            endpoint_path=prepared.endpoint_path,
            dataset_id=BEA_NIPA_HISTORY_EVIDENCE_DATASET_ID,
            response_bytes=response_bytes,
            response_sha256=parsed.raw_bytes_sha256,
            row_count=len(parsed.rows),
            normalization_version=BEA_NIPA_NORMALIZATION_VERSION,
            request_scope=request_scope,
        )
        capture_id = _row_text(row, "capture_id")
        artifact_id = _row_text(row, "artifact_id")
        snapshot_id = _row_text(row, "snapshot_id")
        run_id = _row_text(row, "run_id")
        captured_at = _canonical_datetime(row["captured_at"], "BEA capture captured_at")
        stored = _BeaStoredCapture(capture_id, artifact_id, snapshot_id, run_id, captured_at, parsed)
        captures.append(stored)
        record_by_capture_id[capture_id] = row

    captures_by_pair: dict[tuple[str, str], list[_BeaStoredCapture]] = {}
    captures_by_origin_run: dict[str, list[_BeaStoredCapture]] = {}
    for item in captures:
        origin = run_rows.get(item.run_id)
        if origin is None:
            raise ValidationError("Stage 11 BEA capture is not owned by a completed canonical run")
        origin_identity = _require_digest(origin["semantic_identity"], "BEA run semantic_identity")
        raw = item.raw
        scope = raw.request_scope
        capture_semantic = _capture_semantic_identity(
            origin_identity,
            scope,
            raw.raw_bytes_sha256,
            raw.semantic_sha256,
            normalized_key="normalized_history_sha256",
        )
        _require_expected_id(item.capture_id, stable_id("stage11_bea_nipa_capture", capture_semantic), "BEA capture_id")
        _require_expected_id(
            item.artifact_id,
            stable_id(
                "stage11_bea_nipa_artifact",
                BEA_NIPA_HISTORY_EVIDENCE_DATASET_ID,
                raw.raw_bytes_sha256,
                _sha256_json(scope),
            ),
            "BEA artifact_id",
        )
        _require_expected_id(item.snapshot_id, stable_id("stage11_bea_nipa_capture_snapshot", capture_semantic), "BEA capture snapshot_id")
        if record_by_capture_id[item.capture_id]["semantic_identity"] != capture_semantic:
            raise ValidationError("Stage 11 BEA capture semantic identity differs from raw evidence")
        if item.captured_at != _canonical_datetime(origin["started_at"], "BEA run started_at"):
            raise ValidationError("Stage 11 BEA capture local availability is not its originating run time")
        _assert_artifact(
            connection,
            artifact_id=item.artifact_id,
            run_id=item.run_id,
            dataset_id=BEA_NIPA_HISTORY_EVIDENCE_DATASET_ID,
            content_sha256=raw.raw_bytes_sha256,
            byte_count=len(raw.raw_bytes),
            request_scope=scope,
            captured_at=item.captured_at,
            normalization_version=BEA_NIPA_NORMALIZATION_VERSION,
            source_reference="bea/nipa_history",
        )
        pair = (raw.request.table_name, raw.request.series_code)
        captures_by_pair.setdefault(pair, []).append(item)
        captures_by_origin_run.setdefault(item.run_id, []).append(item)

    reconciled: list[_BeaRun] = []
    combinations: dict[str, list[tuple[BeaNipaHistoryCohort, tuple[_BeaStoredCapture, ...], dict[str, object]]]] = {}
    first_options = captures_by_pair.get(BEA_NIPA_TABLE_SERIES[0], ())
    second_options = captures_by_pair.get(BEA_NIPA_TABLE_SERIES[1], ())
    if not first_options or not second_options:
        raise ValidationError("Stage 11 BEA retained evidence lacks an approved table")
    if len(first_options) * len(second_options) > 100_000:
        raise ResourceLimitError("Stage 11 BEA reconciliation candidate space exceeds its bound")
    for first, second in itertools.product(first_options, second_options):
        ordered = (first, second)
        cohort = assemble_bea_nipa_history(tuple(item.raw for item in ordered))
        request_scope = {
            "provider": "bea",
            "availability_basis": "local_capture",
            "requests": [item.raw.request_scope for item in ordered],
        }
        semantic_identity = _publication_identity(
            collector_id=BEA_NIPA_HISTORY_COLLECTOR_ID,
            dataset_id=BEA_NIPA_HISTORY_DATASET_ID,
            request_scope=request_scope,
            normalization_version=BEA_NIPA_NORMALIZATION_VERSION,
            normalized_sha256=cohort.semantic_sha256,
        )
        combinations.setdefault(semantic_identity, []).append((cohort, ordered, request_scope))
    for run_id, run in run_rows.items():
        semantic_identity = _require_digest(run["semantic_identity"], "BEA run semantic_identity")
        matches = combinations.get(semantic_identity, ())
        if len(matches) != 1:
            raise ValidationError("Stage 11 BEA run cannot be reconstructed as one exact two-table cohort")
        cohort, ordered, request_scope = matches[0]
        expected_run_id = stable_id("stage11_bea_nipa_run", BEA_NIPA_HISTORY_DATASET_ID, semantic_identity)
        expected_snapshot_id = stable_id("stage11_bea_nipa_snapshot", BEA_NIPA_HISTORY_DATASET_ID, semantic_identity)
        _require_expected_id(run_id, expected_run_id, "BEA run_id")
        newly_captured = tuple(item for item in ordered if item.run_id == run_id)
        if not newly_captured or tuple(captures_by_origin_run.get(run_id, ())) != newly_captured:
            raise ValidationError("Stage 11 BEA run capture bindings do not match its complete replay cohort")
        run_captured_at = _canonical_datetime(run["started_at"], "BEA run started_at")
        _assert_run_and_snapshot(
            connection,
            run_id=run_id,
            snapshot_id=expected_snapshot_id,
            dataset_id=BEA_NIPA_HISTORY_DATASET_ID,
            semantic_identity=semantic_identity,
            command=BEA_NIPA_HISTORY_COLLECTOR_ID,
            request_scope=request_scope,
            captured_at=run_captured_at,
            fetched_count=len(cohort.rows),
            output_datasets=_BEA_OUTPUT_DATASETS,
            artifact_ids=tuple(item.artifact_id for item in newly_captured),
        )
        reconciled.append(_BeaRun(run_id, run_captured_at, cohort, ordered, semantic_identity))
    return tuple(sorted(reconciled, key=lambda item: (item.captured_at, item.run_id)))


def _load_retail_runs(connection: sqlite3.Connection) -> tuple[_RetailRun, ...]:
    rows = tuple(
        connection.execute(
            """
            SELECT capture_id, dataset_id, provider, endpoint_path, request_scope_json,
                   request_scope_sha256, response_sha256, response_bytes,
                   semantic_identity, completeness, availability_basis, state_id, sector_id,
                   page_offset, page_length, page_total, artifact_id, snapshot_id,
                   captured_at, captured_precision, row_count, normalization_version, run_id
            FROM stage11_eia_retail_captures
            ORDER BY captured_at, run_id, page_offset, capture_id
            """
        )
    )
    if not rows:
        raise ValidationError("Stage 11 EIA retail evidence is missing")
    run_rows = {
        _row_text(row, "run_id"): row
        for row in connection.execute(
            """
            SELECT run_id, dataset_id, semantic_identity, command, scope_json, status,
                   started_at, completed_at, snapshot_id, fetched_count
            FROM ingestion_runs
            WHERE dataset_id=? AND status='succeeded'
            ORDER BY started_at, run_id
            """,
            (EIA_ELECTRICITY_RETAIL_HISTORY_DATASET_ID,),
        )
    }
    if not run_rows:
        raise ValidationError("Stage 11 EIA retail publication runs are missing")
    pages: list[_RetailStoredPage] = []
    record_by_capture_id: dict[str, sqlite3.Row] = {}
    for row in rows:
        offset = row["page_offset"]
        _require_int(offset, "retail page_offset", minimum=0)
        prepared = prepare_eia_retail_page(offset)
        if (
            row["state_id"] != EIA_RETAIL_STATE_ID
            or row["sector_id"] != EIA_RETAIL_SECTOR_ID
            or row["page_length"] != 5_000
        ):
            raise ValidationError("Stage 11 EIA retail capture is outside the reviewed US/ALL pagination scope")
        response_bytes = _row_bytes(row, "response_bytes", "EIA retail response_bytes", maximum=16_777_216)
        parsed = parse_eia_retail_page_response(body=response_bytes, prepared=prepared)
        if parsed.raw_bytes != response_bytes or parsed.raw_bytes_sha256 != row["response_sha256"]:
            raise ValidationError("Stage 11 EIA retail raw page differs after reparsing")
        if row["page_total"] != parsed.total:
            raise ValidationError("Stage 11 EIA retail page total differs from raw evidence")
        request_scope = prepared.request_scope()
        _assert_capture_fields(
            row,
            provider="eia",
            endpoint_path=prepared.endpoint_path,
            dataset_id=EIA_ELECTRICITY_RETAIL_HISTORY_EVIDENCE_DATASET_ID,
            response_bytes=response_bytes,
            response_sha256=parsed.raw_bytes_sha256,
            row_count=parsed.raw_row_count,
            normalization_version=EIA_RETAIL_NORMALIZATION_VERSION,
            request_scope=request_scope,
        )
        capture_id = _row_text(row, "capture_id")
        artifact_id = _row_text(row, "artifact_id")
        snapshot_id = _row_text(row, "snapshot_id")
        run_id = _row_text(row, "run_id")
        captured_at = _canonical_datetime(row["captured_at"], "retail capture captured_at")
        stored = _RetailStoredPage(capture_id, artifact_id, snapshot_id, run_id, captured_at, parsed)
        pages.append(stored)
        record_by_capture_id[capture_id] = row

    pages_by_offset: dict[int, list[_RetailStoredPage]] = {}
    pages_by_origin_run: dict[str, list[_RetailStoredPage]] = {}
    for item in pages:
        origin = run_rows.get(item.run_id)
        if origin is None:
            raise ValidationError("Stage 11 EIA retail capture is not owned by a completed canonical run")
        origin_identity = _require_digest(origin["semantic_identity"], "EIA retail run semantic_identity")
        raw = item.raw
        scope = raw.request.request_scope()
        capture_semantic = _capture_semantic_identity(
            origin_identity,
            scope,
            raw.raw_bytes_sha256,
            raw.semantic_sha256,
            normalized_key="normalized_page_sha256",
        )
        _require_expected_id(item.capture_id, stable_id("stage11_eia_retail_capture", capture_semantic), "EIA retail capture_id")
        _require_expected_id(
            item.artifact_id,
            stable_id(
                "stage11_eia_retail_artifact",
                EIA_ELECTRICITY_RETAIL_HISTORY_EVIDENCE_DATASET_ID,
                raw.raw_bytes_sha256,
                _sha256_json(scope),
            ),
            "EIA retail artifact_id",
        )
        _require_expected_id(item.snapshot_id, stable_id("stage11_eia_retail_capture_snapshot", capture_semantic), "EIA retail capture snapshot_id")
        if record_by_capture_id[item.capture_id]["semantic_identity"] != capture_semantic:
            raise ValidationError("Stage 11 EIA retail capture semantic identity differs from raw evidence")
        if item.captured_at != _canonical_datetime(origin["started_at"], "EIA retail run started_at"):
            raise ValidationError("Stage 11 EIA retail capture local availability is not its originating run time")
        _assert_artifact(
            connection,
            artifact_id=item.artifact_id,
            run_id=item.run_id,
            dataset_id=EIA_ELECTRICITY_RETAIL_HISTORY_EVIDENCE_DATASET_ID,
            content_sha256=raw.raw_bytes_sha256,
            byte_count=len(raw.raw_bytes),
            request_scope=scope,
            captured_at=item.captured_at,
            normalization_version=EIA_RETAIL_NORMALIZATION_VERSION,
            source_reference="eia/electricity_retail_history",
        )
        pages_by_offset.setdefault(raw.request.offset, []).append(item)
        pages_by_origin_run.setdefault(item.run_id, []).append(item)

    reconciled: list[_RetailRun] = []
    for run_id, run in run_rows.items():
        semantic_identity = _require_digest(run["semantic_identity"], "EIA retail run semantic_identity")
        run_scope = _strict_json_object(run["scope_json"], "EIA retail run scope_json")
        offsets = run_scope.get("page_offsets")
        total = run_scope.get("pagination_total")
        if (
            not isinstance(offsets, list)
            or not offsets
            or any(isinstance(value, bool) or not isinstance(value, int) or value < 0 or value % 5_000 for value in offsets)
            or offsets != sorted(set(offsets))
            or isinstance(total, bool)
            or not isinstance(total, int)
            or total < 1
            or total > 40_000
        ):
            raise ValidationError("Stage 11 EIA retail run has invalid complete pagination scope")
        expected_offsets = list(range(0, total, 5_000))
        if offsets != expected_offsets:
            raise ValidationError("Stage 11 EIA retail run scope does not prove gap-free pagination")
        options = tuple(
            tuple(page for page in pages_by_offset.get(offset, ()) if page.raw.total == total)
            for offset in offsets
        )
        if any(not group for group in options):
            raise ValidationError("Stage 11 EIA retail run lacks retained evidence for a required page")
        combination_count = 1
        for group in options:
            combination_count *= len(group)
            if combination_count > 100_000:
                raise ResourceLimitError("Stage 11 EIA retail reconciliation candidate space exceeds its bound")
        matches: list[tuple[EiaRetailCapture, tuple[_RetailStoredPage, ...], dict[str, object]]] = []
        for selected in itertools.product(*options):
            ordered = tuple(sorted(selected, key=lambda item: item.raw.request.offset))
            cohort = assemble_eia_retail_capture(tuple(item.raw for item in ordered))
            request_scope = dict(cohort.request_scope)
            request_scope["availability_basis"] = "local_capture"
            candidate_identity = _publication_identity(
                collector_id=EIA_ELECTRICITY_RETAIL_HISTORY_COLLECTOR_ID,
                dataset_id=EIA_ELECTRICITY_RETAIL_HISTORY_DATASET_ID,
                request_scope=request_scope,
                normalization_version=EIA_RETAIL_NORMALIZATION_VERSION,
                normalized_sha256=cohort.semantic_sha256,
            )
            if candidate_identity == semantic_identity:
                matches.append((cohort, ordered, request_scope))
        if len(matches) != 1:
            raise ValidationError("Stage 11 EIA retail run cannot be reconstructed as one exact complete page cohort")
        cohort, ordered, request_scope = matches[0]
        expected_run_id = stable_id("stage11_eia_retail_run", EIA_ELECTRICITY_RETAIL_HISTORY_DATASET_ID, semantic_identity)
        expected_snapshot_id = stable_id("stage11_eia_retail_snapshot", EIA_ELECTRICITY_RETAIL_HISTORY_DATASET_ID, semantic_identity)
        _require_expected_id(run_id, expected_run_id, "EIA retail run_id")
        newly_captured = tuple(item for item in ordered if item.run_id == run_id)
        if not newly_captured or tuple(pages_by_origin_run.get(run_id, ())) != newly_captured:
            raise ValidationError("Stage 11 EIA retail run capture bindings do not match its complete replay cohort")
        run_captured_at = _canonical_datetime(run["started_at"], "EIA retail run started_at")
        _assert_run_and_snapshot(
            connection,
            run_id=run_id,
            snapshot_id=expected_snapshot_id,
            dataset_id=EIA_ELECTRICITY_RETAIL_HISTORY_DATASET_ID,
            semantic_identity=semantic_identity,
            command=EIA_ELECTRICITY_RETAIL_HISTORY_COLLECTOR_ID,
            request_scope=request_scope,
            captured_at=run_captured_at,
            fetched_count=len(cohort.rows),
            output_datasets=_EIA_RETAIL_OUTPUT_DATASETS,
            artifact_ids=tuple(item.artifact_id for item in newly_captured),
        )
        reconciled.append(_RetailRun(run_id, run_captured_at, cohort, ordered, semantic_identity))
    return tuple(sorted(reconciled, key=lambda item: (item.captured_at, item.run_id)))


def _load_weekly_runs(connection: sqlite3.Connection) -> tuple[_WeeklyRun, ...]:
    rows = tuple(
        connection.execute(
            """
            SELECT capture_id, dataset_id, provider, endpoint_path, provider_series_id,
                   request_scope_json, request_scope_sha256, response_sha256,
                   response_bytes, semantic_identity, completeness, availability_basis,
                   artifact_id, snapshot_id, captured_at, captured_precision, row_count,
                   normalization_version, run_id
            FROM stage11_eia_weekly_captures
            ORDER BY captured_at, run_id, capture_id
            """
        )
    )
    if not rows:
        raise ValidationError("Stage 11 EIA weekly evidence is missing")
    reconciled: list[_WeeklyRun] = []
    seen_runs: set[str] = set()
    for row in rows:
        if row["provider_series_id"] != EIA_WEEKLY_SERIES_ID:
            raise ValidationError("Stage 11 EIA weekly capture has an unexpected provider series")
        prepared = prepare_eia_weekly_capture()
        response_bytes = _row_bytes(row, "response_bytes", "EIA weekly response_bytes", maximum=16_777_216)
        parsed = parse_eia_weekly_response(body=response_bytes, prepared=prepared)
        if parsed.raw_bytes != response_bytes or parsed.raw_bytes_sha256 != row["response_sha256"]:
            raise ValidationError("Stage 11 EIA weekly raw response differs after reparsing")
        request_scope = parsed.request_scope
        _assert_capture_fields(
            row,
            provider="eia",
            endpoint_path=prepared.endpoint_path,
            dataset_id=EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_EVIDENCE_DATASET_ID,
            response_bytes=response_bytes,
            response_sha256=parsed.raw_bytes_sha256,
            row_count=len(parsed.rows),
            normalization_version=EIA_WEEKLY_NORMALIZATION_VERSION,
            request_scope=request_scope,
        )
        capture_id = _row_text(row, "capture_id")
        artifact_id = _row_text(row, "artifact_id")
        snapshot_id = _row_text(row, "snapshot_id")
        run_id = _row_text(row, "run_id")
        if run_id in seen_runs:
            raise ValidationError("Stage 11 EIA weekly run contains more than one capture")
        seen_runs.add(run_id)
        captured_at = _canonical_datetime(row["captured_at"], "weekly capture captured_at")
        request_scope_with_basis = dict(request_scope)
        request_scope_with_basis["availability_basis"] = "local_capture"
        semantic_identity = _publication_identity(
            collector_id=EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_COLLECTOR_ID,
            dataset_id=EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_DATASET_ID,
            request_scope=request_scope_with_basis,
            normalization_version=EIA_WEEKLY_NORMALIZATION_VERSION,
            normalized_sha256=parsed.semantic_sha256,
        )
        expected_run_id = stable_id("stage11_eia_weekly_run", EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_DATASET_ID, semantic_identity)
        expected_snapshot_id = stable_id("stage11_eia_weekly_snapshot", EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_DATASET_ID, semantic_identity)
        _require_expected_id(run_id, expected_run_id, "EIA weekly run_id")
        capture_semantic = _capture_semantic_identity(
            semantic_identity,
            request_scope,
            parsed.raw_bytes_sha256,
            parsed.semantic_sha256,
            normalized_key="normalized_history_sha256",
        )
        _require_expected_id(capture_id, stable_id("stage11_eia_weekly_capture", capture_semantic), "EIA weekly capture_id")
        _require_expected_id(
            artifact_id,
            stable_id(
                "stage11_eia_weekly_artifact",
                EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_EVIDENCE_DATASET_ID,
                parsed.raw_bytes_sha256,
                _sha256_json(request_scope),
            ),
            "EIA weekly artifact_id",
        )
        _require_expected_id(snapshot_id, stable_id("stage11_eia_weekly_capture_snapshot", capture_semantic), "EIA weekly capture snapshot_id")
        if row["semantic_identity"] != capture_semantic:
            raise ValidationError("Stage 11 EIA weekly capture semantic identity differs from raw evidence")
        _assert_run_and_snapshot(
            connection,
            run_id=run_id,
            snapshot_id=expected_snapshot_id,
            dataset_id=EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_DATASET_ID,
            semantic_identity=semantic_identity,
            command=EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_COLLECTOR_ID,
            request_scope=request_scope_with_basis,
            captured_at=captured_at,
            fetched_count=len(parsed.rows),
            output_datasets=_EIA_WEEKLY_OUTPUT_DATASETS,
            artifact_ids=(artifact_id,),
        )
        _assert_artifact(
            connection,
            artifact_id=artifact_id,
            run_id=run_id,
            dataset_id=EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_EVIDENCE_DATASET_ID,
            content_sha256=parsed.raw_bytes_sha256,
            byte_count=len(parsed.raw_bytes),
            request_scope=request_scope,
            captured_at=captured_at,
            normalization_version=EIA_WEEKLY_NORMALIZATION_VERSION,
            source_reference="eia/petroleum_weekly_stock_history",
        )
        reconciled.append(
            _WeeklyRun(
                run_id,
                captured_at,
                parsed,
                _WeeklyStoredCapture(capture_id, artifact_id, snapshot_id, run_id, captured_at, parsed),
                semantic_identity,
            )
        )
    return tuple(sorted(reconciled, key=lambda item: (item.captured_at, item.run_id)))


def _rows_by_key(rows: Iterable[sqlite3.Row], key_names: tuple[str, ...]) -> dict[tuple[str, ...], list[sqlite3.Row]]:
    result: dict[tuple[str, ...], list[sqlite3.Row]] = {}
    for row in rows:
        key = tuple(_row_text(row, name) for name in key_names)
        result.setdefault(key, []).append(row)
    return result


def _verify_bea_versions(connection: sqlite3.Connection, runs: tuple[_BeaRun, ...]) -> tuple[int, int, tuple[Mapping[str, object], ...]]:
    rows = tuple(
        connection.execute(
            """
            SELECT version_id, canonical_series_id, table_name, series_code, period,
                   value_text, unit, unit_multiplier, available_at, available_precision,
                   captured_at, captured_precision, correction_sequence,
                   supersedes_version_id, capture_id, artifact_id, snapshot_id, run_id,
                   source_row
            FROM stage11_bea_nipa_observation_versions
            ORDER BY canonical_series_id, period, correction_sequence
            """
        )
    )
    current_rows = tuple(
        connection.execute(
            "SELECT canonical_series_id, period, current_version_id FROM stage11_bea_nipa_observations ORDER BY canonical_series_id, period"
        )
    )
    if not rows or not current_rows:
        raise ValidationError("Stage 11 BEA canonical history is missing")
    captures = {capture.capture_id: capture for run in runs for capture in run.captures}
    raw_rows = {
        capture.capture_id: {
            (item.canonical_series_id, item.period): item
            for item in capture.raw.rows
        }
        for capture in captures.values()
    }
    histories = _rows_by_key(rows, ("canonical_series_id", "period"))
    current = {
        (_row_text(row, "canonical_series_id"), _row_text(row, "period")): _row_text(row, "current_version_id")
        for row in current_rows
    }
    if set(current) != set(histories):
        raise ValidationError("Stage 11 BEA current projection coverage is incomplete")
    for key, history in histories.items():
        for expected_sequence, version in enumerate(history, start=1):
            capture = captures.get(version["capture_id"])
            if capture is None or version["run_id"] != capture.run_id:
                raise ValidationError("Stage 11 BEA version has unknown capture lineage")
            raw = raw_rows[capture.capture_id].get(key)
            if raw is None or version["source_row"] != raw.source_row:
                raise ValidationError("Stage 11 BEA version is not bound to a parsed raw row")
            expected = (
                raw.table_name,
                raw.series_code,
                _decimal_text(raw.value, "BEA raw value"),
                raw.cl_unit,
                int(raw.unit_mult),
            )
            actual = (
                version["table_name"],
                version["series_code"],
                _decimal_text(version["value_text"], "BEA canonical value"),
                version["unit"],
                version["unit_multiplier"],
            )
            if actual != expected:
                raise ValidationError("Stage 11 BEA canonical version differs from parsed raw evidence")
            if (
                version["canonical_series_id"] != raw.canonical_series_id
                or version["correction_sequence"] != expected_sequence
                or version["captured_at"] != capture.captured_at
                or version["available_at"] != capture.captured_at
                or version["available_precision"] != "datetime"
                or version["captured_precision"] != "datetime"
                or version["artifact_id"] != capture.artifact_id
                or version["snapshot_id"] != capture.snapshot_id
                or (expected_sequence == 1 and version["supersedes_version_id"] is not None)
                or (expected_sequence > 1 and version["supersedes_version_id"] != history[expected_sequence - 2]["version_id"])
            ):
                raise ValidationError("Stage 11 BEA correction lineage is invalid")
            _canonical_datetime(version["captured_at"], "BEA version captured_at")
            _canonical_datetime(version["available_at"], "BEA version available_at")
        if current[key] != history[-1]["version_id"]:
            raise ValidationError("Stage 11 BEA current pointer is not the latest correction")
    latest = runs[-1]
    expected_latest = {(item.canonical_series_id, item.period): item for item in latest.cohort.rows}
    if set(current) != set(expected_latest):
        raise ValidationError("Stage 11 BEA latest complete cohort does not cover current rows")
    for key, raw in expected_latest.items():
        version = histories[key][-1]
        if (
            _decimal_text(version["value_text"], "BEA current value") != _decimal_text(raw.value, "BEA raw value")
            or version["unit"] != raw.cl_unit
            or version["unit_multiplier"] != int(raw.unit_mult)
        ):
            raise ValidationError("Stage 11 BEA current row is not the latest complete raw cohort")
    summaries = tuple(
        {
            "canonical_series_id": key[0],
            "period": key[1],
            "version_count": len(history),
            "current_version_sha256": _sha256_json(
                {
                    "value_text": _decimal_text(history[-1]["value_text"], "BEA current value"),
                    "unit": history[-1]["unit"],
                    "unit_multiplier": history[-1]["unit_multiplier"],
                    "correction_sequence": history[-1]["correction_sequence"],
                }
            ),
        }
        for key, history in sorted(histories.items())
    )
    return len(rows), len(current_rows), summaries


def _verify_retail_versions(connection: sqlite3.Connection, runs: tuple[_RetailRun, ...]) -> tuple[int, int, tuple[Mapping[str, object], ...]]:
    rows = tuple(
        connection.execute(
            """
            SELECT version_id, canonical_series_id, metric, period, state_id, sector_id,
                   value_text, unit, available_at, available_precision, captured_at,
                   captured_precision, correction_sequence, supersedes_version_id,
                   capture_id, artifact_id, snapshot_id, run_id, source_row
            FROM stage11_eia_retail_observation_versions
            ORDER BY canonical_series_id, period, state_id, sector_id, correction_sequence
            """
        )
    )
    current_rows = tuple(
        connection.execute(
            """
            SELECT canonical_series_id, period, state_id, sector_id, current_version_id
            FROM stage11_eia_retail_observations
            ORDER BY canonical_series_id, period, state_id, sector_id
            """
        )
    )
    if not rows or not current_rows:
        raise ValidationError("Stage 11 EIA retail canonical history is missing")
    captures = {page.capture_id: page for run in runs for page in run.pages}
    raw_rows = {
        page.capture_id: {(item.canonical_series_id, item.period, EIA_RETAIL_STATE_ID, EIA_RETAIL_SECTOR_ID): item for item in page.raw.rows}
        for page in captures.values()
    }
    key_names = ("canonical_series_id", "period", "state_id", "sector_id")
    histories = _rows_by_key(rows, key_names)
    current = {tuple(_row_text(row, item) for item in key_names): _row_text(row, "current_version_id") for row in current_rows}
    if set(current) != set(histories):
        raise ValidationError("Stage 11 EIA retail current projection coverage is incomplete")
    for key, history in histories.items():
        for expected_sequence, version in enumerate(history, start=1):
            capture = captures.get(version["capture_id"])
            if capture is None or version["run_id"] != capture.run_id:
                raise ValidationError("Stage 11 EIA retail version has unknown capture lineage")
            raw = raw_rows[capture.capture_id].get(key)
            if raw is None or version["source_row"] != raw.source_row:
                raise ValidationError("Stage 11 EIA retail version is not bound to a parsed raw row")
            if (
                version["metric"] != raw.metric
                or version["canonical_series_id"] != raw.canonical_series_id
                or _decimal_text(version["value_text"], "retail canonical value") != _decimal_text(raw.value, "retail raw value")
                or version["unit"] != raw.unit
                or version["state_id"] != EIA_RETAIL_STATE_ID
                or version["sector_id"] != EIA_RETAIL_SECTOR_ID
                or version["correction_sequence"] != expected_sequence
                or version["captured_at"] != capture.captured_at
                or version["available_at"] != capture.captured_at
                or version["available_precision"] != "datetime"
                or version["captured_precision"] != "datetime"
                or version["artifact_id"] != capture.artifact_id
                or version["snapshot_id"] != capture.snapshot_id
                or (expected_sequence == 1 and version["supersedes_version_id"] is not None)
                or (expected_sequence > 1 and version["supersedes_version_id"] != history[expected_sequence - 2]["version_id"])
            ):
                raise ValidationError("Stage 11 EIA retail correction lineage is invalid")
            _canonical_datetime(version["captured_at"], "retail version captured_at")
            _canonical_datetime(version["available_at"], "retail version available_at")
        if current[key] != history[-1]["version_id"]:
            raise ValidationError("Stage 11 EIA retail current pointer is not the latest correction")
    latest = runs[-1]
    expected_latest = {
        (item.canonical_series_id, item.period, EIA_RETAIL_STATE_ID, EIA_RETAIL_SECTOR_ID): item
        for item in latest.cohort.rows
    }
    if set(current) != set(expected_latest):
        raise ValidationError("Stage 11 EIA retail latest complete pagination does not cover current rows")
    for key, raw in expected_latest.items():
        version = histories[key][-1]
        if (
            _decimal_text(version["value_text"], "retail current value") != _decimal_text(raw.value, "retail raw value")
            or version["unit"] != raw.unit
        ):
            raise ValidationError("Stage 11 EIA retail current row is not the latest complete raw cohort")
    summaries = tuple(
        {
            "canonical_series_id": key[0],
            "period": key[1],
            "state_id": key[2],
            "sector_id": key[3],
            "version_count": len(history),
            "current_version_sha256": _sha256_json(
                {
                    "value_text": _decimal_text(history[-1]["value_text"], "retail current value"),
                    "unit": history[-1]["unit"],
                    "correction_sequence": history[-1]["correction_sequence"],
                }
            ),
        }
        for key, history in sorted(histories.items())
    )
    return len(rows), len(current_rows), summaries


def _verify_weekly_versions(connection: sqlite3.Connection, runs: tuple[_WeeklyRun, ...]) -> tuple[int, int, tuple[Mapping[str, object], ...]]:
    rows = tuple(
        connection.execute(
            """
            SELECT version_id, canonical_series_id, provider_series_id, period, value_text,
                   unit, content_sha256, available_at, available_precision, captured_at,
                   captured_precision, correction_sequence, supersedes_version_id,
                   capture_id, artifact_id, snapshot_id, run_id, source_row
            FROM stage11_eia_weekly_observation_versions
            ORDER BY canonical_series_id, period, correction_sequence
            """
        )
    )
    current_rows = tuple(
        connection.execute(
            "SELECT canonical_series_id, period, current_version_id FROM stage11_eia_weekly_observations ORDER BY canonical_series_id, period"
        )
    )
    if not rows or not current_rows:
        raise ValidationError("Stage 11 EIA weekly canonical history is missing")
    captures = {run.stored.capture_id: run.stored for run in runs}
    raw_rows = {
        run.stored.capture_id: {(item.canonical_series_id, item.period): item for item in run.capture.rows}
        for run in runs
    }
    histories = _rows_by_key(rows, ("canonical_series_id", "period"))
    current = {
        (_row_text(row, "canonical_series_id"), _row_text(row, "period")): _row_text(row, "current_version_id")
        for row in current_rows
    }
    if set(current) != set(histories):
        raise ValidationError("Stage 11 EIA weekly current projection coverage is incomplete")
    for key, history in histories.items():
        for expected_sequence, version in enumerate(history, start=1):
            capture = captures.get(version["capture_id"])
            if capture is None or version["run_id"] != capture.run_id:
                raise ValidationError("Stage 11 EIA weekly version has unknown capture lineage")
            raw = raw_rows[capture.capture_id].get(key)
            if raw is None or version["source_row"] != raw.source_row:
                raise ValidationError("Stage 11 EIA weekly version is not bound to a parsed raw row")
            if (
                version["canonical_series_id"] != EIA_WEEKLY_CANONICAL_SERIES_ID
                or version["provider_series_id"] != EIA_WEEKLY_SERIES_ID
                or _decimal_text(version["value_text"], "weekly canonical value") != _decimal_text(raw.value, "weekly raw value")
                or version["unit"] != raw.unit
                or version["content_sha256"] != raw.content_sha256
                or version["correction_sequence"] != expected_sequence
                or version["captured_at"] != capture.captured_at
                or version["available_at"] != capture.captured_at
                or version["available_precision"] != "datetime"
                or version["captured_precision"] != "datetime"
                or version["artifact_id"] != capture.artifact_id
                or version["snapshot_id"] != capture.snapshot_id
                or (expected_sequence == 1 and version["supersedes_version_id"] is not None)
                or (expected_sequence > 1 and version["supersedes_version_id"] != history[expected_sequence - 2]["version_id"])
            ):
                raise ValidationError("Stage 11 EIA weekly correction lineage is invalid")
            _canonical_datetime(version["captured_at"], "weekly version captured_at")
            _canonical_datetime(version["available_at"], "weekly version available_at")
        if current[key] != history[-1]["version_id"]:
            raise ValidationError("Stage 11 EIA weekly current pointer is not the latest correction")
    latest = runs[-1]
    expected_latest = {(item.canonical_series_id, item.period): item for item in latest.capture.rows}
    if set(current) != set(expected_latest):
        raise ValidationError("Stage 11 EIA weekly latest complete history does not cover current rows")
    for key, raw in expected_latest.items():
        version = histories[key][-1]
        if (
            _decimal_text(version["value_text"], "weekly current value") != _decimal_text(raw.value, "weekly raw value")
            or version["unit"] != raw.unit
            or version["content_sha256"] != raw.content_sha256
        ):
            raise ValidationError("Stage 11 EIA weekly current row is not the latest complete raw cohort")
    summaries = tuple(
        {
            "canonical_series_id": key[0],
            "period": key[1],
            "version_count": len(history),
            "current_version_sha256": _sha256_json(
                {
                    "value_text": _decimal_text(history[-1]["value_text"], "weekly current value"),
                    "unit": history[-1]["unit"],
                    "content_sha256": history[-1]["content_sha256"],
                    "correction_sequence": history[-1]["correction_sequence"],
                }
            ),
        }
        for key, history in sorted(histories.items())
    )
    return len(rows), len(current_rows), summaries


@dataclass(frozen=True, slots=True)
class Stage11MacroReconciliation:
    """Path-free proof for dedicated Stage 11 macro relations only."""

    scope_manifest_sha256: str
    target_profile_id: str
    registry_schema_version: str
    registry_version: str
    registry_source_sha256: str
    migration_id: str
    migration_sha256: str
    bea_cohort_count: int
    bea_capture_count: int
    bea_version_count: int
    bea_current_count: int
    retail_cohort_count: int
    retail_capture_count: int
    retail_version_count: int
    retail_current_count: int
    weekly_capture_count: int
    weekly_version_count: int
    weekly_current_count: int
    bea_current_rows: tuple[Mapping[str, object], ...]
    retail_current_rows: tuple[Mapping[str, object], ...]
    weekly_current_rows: tuple[Mapping[str, object], ...]
    raw_captures_reparsed: bool
    exact_request_scopes_verified: bool
    complete_bea_cohorts_verified: bool
    complete_retail_pagination_verified: bool
    exact_weekly_series_verified: bool
    canonical_rows_match_raw: bool
    correction_lineage_verified: bool
    local_capture_availability_only: bool
    no_post_cutoff_invention: bool
    stage3_relations_reinterpreted: bool
    stores_unchanged: bool
    sha256: str

    def __post_init__(self) -> None:
        for field_name in (
            "scope_manifest_sha256",
            "registry_source_sha256",
            "migration_sha256",
            "sha256",
        ):
            _require_digest(getattr(self, field_name), field_name)
        for field_name in (
            "target_profile_id",
            "registry_schema_version",
            "registry_version",
            "migration_id",
        ):
            _require_text(getattr(self, field_name), field_name)
        if (
            self.scope_manifest_sha256 != REVIEWED_STAGE11_MACRO_SCOPE_SHA256
            or self.target_profile_id != STAGE11_TARGET_PROFILE_ID
            or self.registry_schema_version != "1.7.0"
            or self.registry_version != "2.9.0"
            or self.migration_id != STAGE11_MIGRATION_ID
            or self.migration_sha256 != STAGE11_MIGRATION_SHA256
        ):
            raise ValidationError("Stage 11 reconciliation contract binding is invalid")
        for field_name in (
            "bea_cohort_count", "bea_capture_count", "bea_version_count", "bea_current_count",
            "retail_cohort_count", "retail_capture_count", "retail_version_count", "retail_current_count",
            "weekly_capture_count", "weekly_version_count", "weekly_current_count",
        ):
            _require_int(getattr(self, field_name), field_name, minimum=1)
        if (
            self.bea_capture_count != self.bea_cohort_count * 2
            or self.bea_version_count < self.bea_current_count
            or self.retail_version_count < self.retail_current_count
            or self.weekly_version_count < self.weekly_current_count
        ):
            raise ValidationError("Stage 11 reconciliation cardinality is invalid")
        for field_name in ("bea_current_rows", "retail_current_rows", "weekly_current_rows"):
            value = getattr(self, field_name)
            if not isinstance(value, tuple) or not value:
                raise ValidationError("Stage 11 reconciliation current-row material is invalid")
            frozen = tuple(_freeze_json(item, field_name) for item in value)
            if not all(isinstance(item, Mapping) for item in frozen):
                raise ValidationError("Stage 11 reconciliation current-row material is invalid")
            object.__setattr__(self, field_name, frozen)
        for field_name in (
            "raw_captures_reparsed",
            "exact_request_scopes_verified",
            "complete_bea_cohorts_verified",
            "complete_retail_pagination_verified",
            "exact_weekly_series_verified",
            "canonical_rows_match_raw",
            "correction_lineage_verified",
            "local_capture_availability_only",
            "no_post_cutoff_invention",
            "stage3_relations_reinterpreted",
            "stores_unchanged",
        ):
            _require_bool(getattr(self, field_name), field_name)
        if (
            not self.raw_captures_reparsed
            or not self.exact_request_scopes_verified
            or not self.complete_bea_cohorts_verified
            or not self.complete_retail_pagination_verified
            or not self.exact_weekly_series_verified
            or not self.canonical_rows_match_raw
            or not self.correction_lineage_verified
            or not self.local_capture_availability_only
            or not self.no_post_cutoff_invention
            or self.stage3_relations_reinterpreted
            or not self.stores_unchanged
        ):
            raise ValidationError("Stage 11 reconciliation safety proof is incomplete")
        if self.sha256 != _sha256_json(self.material()):
            raise ValidationError("Stage 11 reconciliation digest is inconsistent")

    @property
    def stage3_reinterpretation_absent(self) -> bool:
        """Compatibility spelling for callers that want a positive predicate."""

        return not self.stage3_relations_reinterpreted

    def material(self) -> dict[str, object]:
        return {
            "contract": "quant_data.stage11_macro_reconciliation",
            "contract_version": "1.0.0",
            "scope_manifest_sha256": self.scope_manifest_sha256,
            "target_profile_id": self.target_profile_id,
            "registry": {
                "schema_version": self.registry_schema_version,
                "registry_version": self.registry_version,
                "source_sha256": self.registry_source_sha256,
            },
            "migration": {"id": self.migration_id, "sha256": self.migration_sha256},
            "bea": {
                "cohort_count": self.bea_cohort_count,
                "capture_count": self.bea_capture_count,
                "version_count": self.bea_version_count,
                "current_count": self.bea_current_count,
                "current_rows": [_thaw_json(item) for item in self.bea_current_rows],
            },
            "retail": {
                "cohort_count": self.retail_cohort_count,
                "capture_count": self.retail_capture_count,
                "version_count": self.retail_version_count,
                "current_count": self.retail_current_count,
                "current_rows": [_thaw_json(item) for item in self.retail_current_rows],
            },
            "weekly": {
                "capture_count": self.weekly_capture_count,
                "version_count": self.weekly_version_count,
                "current_count": self.weekly_current_count,
                "current_rows": [_thaw_json(item) for item in self.weekly_current_rows],
            },
            "raw_captures_reparsed": self.raw_captures_reparsed,
            "exact_request_scopes_verified": self.exact_request_scopes_verified,
            "complete_bea_cohorts_verified": self.complete_bea_cohorts_verified,
            "complete_retail_pagination_verified": self.complete_retail_pagination_verified,
            "exact_weekly_series_verified": self.exact_weekly_series_verified,
            "canonical_rows_match_raw": self.canonical_rows_match_raw,
            "correction_lineage_verified": self.correction_lineage_verified,
            "local_capture_availability_only": self.local_capture_availability_only,
            "no_post_cutoff_invention": self.no_post_cutoff_invention,
            "stage3_relations_reinterpreted": self.stage3_relations_reinterpreted,
            "stores_unchanged": self.stores_unchanged,
        }

    def to_primitive(self) -> dict[str, object]:
        return {**self.material(), "sha256": self.sha256}


def _reconcile_connection(connection: sqlite3.Connection, registry: Registry, scope: Stage11MacroScope) -> _ReconciledMaterial:
    # No Stage 3 relation is queried here: all facts originate in the dedicated
    # Stage 11 tables and their local evidence bindings.
    bea_runs = _load_bea_runs(connection)
    retail_runs = _load_retail_runs(connection)
    weekly_runs = _load_weekly_runs(connection)
    bea_versions, bea_current, bea_rows = _verify_bea_versions(connection, bea_runs)
    retail_versions, retail_current, retail_rows = _verify_retail_versions(connection, retail_runs)
    weekly_versions, weekly_current, weekly_rows = _verify_weekly_versions(connection, weekly_runs)
    material = {
        "scope_manifest_sha256": scope.manifest_sha256,
        "target_profile_id": scope.target_profile_id,
        "registry_schema_version": registry.schema_version,
        "registry_version": registry.registry_version,
        "registry_source_sha256": registry.source_sha256,
        "migration_id": STAGE11_MIGRATION_ID,
        "migration_sha256": STAGE11_MIGRATION_SHA256,
        "bea_cohort_count": len(bea_runs),
        "bea_capture_count": sum(len(run.captures) for run in bea_runs),
        "bea_version_count": bea_versions,
        "bea_current_count": bea_current,
        "retail_cohort_count": len(retail_runs),
        "retail_capture_count": sum(len(run.pages) for run in retail_runs),
        "retail_version_count": retail_versions,
        "retail_current_count": retail_current,
        "weekly_capture_count": len(weekly_runs),
        "weekly_version_count": weekly_versions,
        "weekly_current_count": weekly_current,
        "bea_current_rows": bea_rows,
        "retail_current_rows": retail_rows,
        "weekly_current_rows": weekly_rows,
        "raw_captures_reparsed": True,
        "exact_request_scopes_verified": True,
        "complete_bea_cohorts_verified": True,
        "complete_retail_pagination_verified": True,
        "exact_weekly_series_verified": True,
        "canonical_rows_match_raw": True,
        "correction_lineage_verified": True,
        "local_capture_availability_only": True,
        "no_post_cutoff_invention": True,
        "stage3_relations_reinterpreted": False,
        "stores_unchanged": True,
    }
    public = Stage11MacroReconciliation(
        **material,
        sha256=_sha256_json(
            {
                "contract": "quant_data.stage11_macro_reconciliation",
                "contract_version": "1.0.0",
                "scope_manifest_sha256": material["scope_manifest_sha256"],
                "target_profile_id": material["target_profile_id"],
                "registry": {
                    "schema_version": material["registry_schema_version"],
                    "registry_version": material["registry_version"],
                    "source_sha256": material["registry_source_sha256"],
                },
                "migration": {"id": material["migration_id"], "sha256": material["migration_sha256"]},
                "bea": {
                    "cohort_count": material["bea_cohort_count"],
                    "capture_count": material["bea_capture_count"],
                    "version_count": material["bea_version_count"],
                    "current_count": material["bea_current_count"],
                    "current_rows": list(material["bea_current_rows"]),
                },
                "retail": {
                    "cohort_count": material["retail_cohort_count"],
                    "capture_count": material["retail_capture_count"],
                    "version_count": material["retail_version_count"],
                    "current_count": material["retail_current_count"],
                    "current_rows": list(material["retail_current_rows"]),
                },
                "weekly": {
                    "capture_count": material["weekly_capture_count"],
                    "version_count": material["weekly_version_count"],
                    "current_count": material["weekly_current_count"],
                    "current_rows": list(material["weekly_current_rows"]),
                },
                **{
                    name: value
                    for name, value in material.items()
                    if name
                    not in {
                        "scope_manifest_sha256", "target_profile_id", "registry_schema_version",
                        "registry_version", "registry_source_sha256", "migration_id", "migration_sha256",
                        "bea_cohort_count", "bea_capture_count", "bea_version_count", "bea_current_count", "bea_current_rows",
                        "retail_cohort_count", "retail_capture_count", "retail_version_count", "retail_current_count", "retail_current_rows",
                        "weekly_capture_count", "weekly_version_count", "weekly_current_count", "weekly_current_rows",
                    }
                },
            }
        ),
    )
    return _ReconciledMaterial(public, bea_runs, retail_runs, weekly_runs)


def _reconcile_material(store_map: StoreMap, registry: Registry, scope: Stage11MacroScope) -> _ReconciledMaterial:
    with read_connection(
        store_map,
        StoreRole.MACRO,
        expected_anchor=registry.store(StoreRole.MACRO.value).anchor_relation,
    ) as connection:
        return _reconcile_connection(connection, registry, scope)


def reconcile_stage11_macro(
    store_map: StoreMap,
    registry: Registry,
    scope: Stage11MacroScope,
) -> Stage11MacroReconciliation:
    """Reconcile one explicit Stage 11 macro candidate without store mutation."""

    if not isinstance(store_map, StoreMap):
        raise ValidationError("Stage 11 reconciliation requires explicit four-store paths")
    validated_registry = _validate_registry(registry)
    validated_scope = _validate_scope(scope)
    before = mutation_fingerprint(store_map)
    result = _reconcile_material(store_map, validated_registry, validated_scope).public
    after = mutation_fingerprint(store_map)
    if before["sha256"] != after["sha256"]:
        raise ValidationError("Stage 11 reconciliation mutated a store")
    return result


def _replay_unchanged(
    store_map: StoreMap,
    registry: Registry,
    material: _ReconciledMaterial,
) -> None:
    """Exercise importer replay only after read-proof that every run exists.

    The importer first does a query-only existing-run check.  We independently
    make that fact explicit before invoking it, then fingerprint both sides so
    any unexpected persistent write fails closed.
    """

    expected = tuple(
        (BEA_NIPA_HISTORY_DATASET_ID, item.semantic_identity) for item in material.bea_runs
    ) + tuple(
        (EIA_ELECTRICITY_RETAIL_HISTORY_DATASET_ID, item.semantic_identity) for item in material.retail_runs
    ) + tuple(
        (EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_DATASET_ID, item.semantic_identity) for item in material.weekly_runs
    )
    with read_connection(
        store_map,
        StoreRole.MACRO,
        expected_anchor=registry.store(StoreRole.MACRO.value).anchor_relation,
    ) as connection:
        for dataset_id, semantic_identity in expected:
            match = connection.execute(
                """
                SELECT 1 FROM ingestion_runs
                WHERE dataset_id=? AND semantic_identity=? AND status='succeeded'
                """,
                (dataset_id, semantic_identity),
            ).fetchone()
            if match is None:
                raise ValidationError("Stage 11 replay has no existing completed semantic run")
    importer = Stage11MacroImporter(store_map, registry, clock=lambda: _REPLAY_CLOCK)
    candidates = tuple(
        importer.prepare_bea_nipa_history(item.cohort) for item in material.bea_runs
    ) + tuple(
        importer.prepare_eia_retail_history(item.cohort) for item in material.retail_runs
    ) + tuple(
        importer.prepare_eia_weekly_history(item.capture) for item in material.weekly_runs
    )
    receipts = tuple(importer.publish_prepared(candidate) for candidate in candidates)
    if any(receipt.outcome != "unchanged" or receipt.written_count != 0 for receipt in receipts):
        raise ValidationError("Stage 11 semantic replay was not unchanged")


@dataclass(frozen=True, slots=True)
class Stage11RepopulationEvidence:
    """Path-free source/restore proof for a private Stage 11 candidate."""

    reconciliation: Stage11MacroReconciliation
    registry_schema_version: str
    registry_version: str
    registry_source_sha256: str
    source_health_sha256: str
    restored_health_sha256: str
    source_logical_manifest_sha256: str
    restored_logical_manifest_sha256: str
    source_mutation_before_sha256: str
    source_mutation_after_sha256: str
    restored_mutation_before_sha256: str
    restored_mutation_after_sha256: str
    source_reconciliation_sha256: str
    restored_reconciliation_sha256: str
    source_restored_equal: bool
    source_reads_unchanged: bool
    restored_reads_unchanged: bool
    raw_reparsed_equal: bool
    complete_bea_cohorts: bool
    complete_retail_pagination: bool
    complete_weekly_series: bool
    canonical_rows_match_raw: bool
    correction_lineage_verified: bool
    no_post_cutoff_invention: bool
    stage3_relations_reinterpreted: bool
    replay_unchanged: bool
    backup_restore_outcome: str
    sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.reconciliation, Stage11MacroReconciliation):
            raise ValidationError("Stage 11 evidence requires macro reconciliation")
        for field_name in ("registry_schema_version", "registry_version", "backup_restore_outcome"):
            _require_text(getattr(self, field_name), field_name)
        for field_name in (
            "registry_source_sha256", "source_health_sha256", "restored_health_sha256",
            "source_logical_manifest_sha256", "restored_logical_manifest_sha256",
            "source_mutation_before_sha256", "source_mutation_after_sha256",
            "restored_mutation_before_sha256", "restored_mutation_after_sha256",
            "source_reconciliation_sha256", "restored_reconciliation_sha256", "sha256",
        ):
            _require_digest(getattr(self, field_name), field_name)
        if (
            self.registry_schema_version != "1.7.0"
            or self.registry_version != "2.9.0"
            or self.registry_source_sha256 != self.reconciliation.registry_source_sha256
            or self.source_reconciliation_sha256 != self.reconciliation.sha256
            or self.backup_restore_outcome != "validated_restored_cohort_equal"
        ):
            raise ValidationError("Stage 11 evidence contract binding is invalid")
        for field_name in (
            "source_restored_equal", "source_reads_unchanged", "restored_reads_unchanged",
            "raw_reparsed_equal", "complete_bea_cohorts", "complete_retail_pagination",
            "complete_weekly_series", "canonical_rows_match_raw", "correction_lineage_verified",
            "no_post_cutoff_invention", "stage3_relations_reinterpreted", "replay_unchanged",
        ):
            _require_bool(getattr(self, field_name), field_name)
        if (
            not self.source_restored_equal
            or not self.source_reads_unchanged
            or not self.restored_reads_unchanged
            or not self.raw_reparsed_equal
            or not self.complete_bea_cohorts
            or not self.complete_retail_pagination
            or not self.complete_weekly_series
            or not self.canonical_rows_match_raw
            or not self.correction_lineage_verified
            or not self.no_post_cutoff_invention
            or self.stage3_relations_reinterpreted
        ):
            raise ValidationError("Stage 11 evidence safety proof is incomplete")
        if self.sha256 != _sha256_json(self.material()):
            raise ValidationError("Stage 11 repopulation evidence digest is inconsistent")

    @property
    def complete_macro_coverage(self) -> bool:
        return self.complete_bea_cohorts and self.complete_retail_pagination and self.complete_weekly_series

    @property
    def stage3_reinterpretation_absent(self) -> bool:
        return not self.stage3_relations_reinterpreted

    def material(self) -> dict[str, object]:
        return {
            "contract": "quant_data.stage11_repopulation_evidence",
            "contract_version": "1.0.0",
            "reconciliation": self.reconciliation.to_primitive(),
            "registry": {
                "schema_version": self.registry_schema_version,
                "registry_version": self.registry_version,
                "source_sha256": self.registry_source_sha256,
            },
            "source_health_sha256": self.source_health_sha256,
            "restored_health_sha256": self.restored_health_sha256,
            "source_logical_manifest_sha256": self.source_logical_manifest_sha256,
            "restored_logical_manifest_sha256": self.restored_logical_manifest_sha256,
            "source_mutation_before_sha256": self.source_mutation_before_sha256,
            "source_mutation_after_sha256": self.source_mutation_after_sha256,
            "restored_mutation_before_sha256": self.restored_mutation_before_sha256,
            "restored_mutation_after_sha256": self.restored_mutation_after_sha256,
            "source_reconciliation_sha256": self.source_reconciliation_sha256,
            "restored_reconciliation_sha256": self.restored_reconciliation_sha256,
            "source_restored_equal": self.source_restored_equal,
            "source_reads_unchanged": self.source_reads_unchanged,
            "restored_reads_unchanged": self.restored_reads_unchanged,
            "raw_reparsed_equal": self.raw_reparsed_equal,
            "complete_bea_cohorts": self.complete_bea_cohorts,
            "complete_retail_pagination": self.complete_retail_pagination,
            "complete_weekly_series": self.complete_weekly_series,
            "canonical_rows_match_raw": self.canonical_rows_match_raw,
            "correction_lineage_verified": self.correction_lineage_verified,
            "no_post_cutoff_invention": self.no_post_cutoff_invention,
            "stage3_relations_reinterpreted": self.stage3_relations_reinterpreted,
            "replay_unchanged": self.replay_unchanged,
            "backup_restore_outcome": self.backup_restore_outcome,
        }

    def to_primitive(self) -> dict[str, object]:
        return {**self.material(), "sha256": self.sha256}


def build_stage11_repopulation_evidence(
    source_map: StoreMap,
    restored_map: StoreMap,
    registry: Registry,
    scope: Stage11MacroScope,
    *,
    replay_unchanged: bool = True,
) -> Stage11RepopulationEvidence:
    """Prove source/restore equality and optional zero-write semantic replay."""

    if not isinstance(source_map, StoreMap) or not isinstance(restored_map, StoreMap):
        raise ValidationError("Stage 11 evidence requires explicit source and restored store maps")
    if not _maps_are_disjoint(source_map, restored_map):
        raise ConflictError("Stage 11 source and restored cohorts must be physically disjoint")
    validated_registry = _validate_registry(registry)
    validated_scope = _validate_scope(scope)
    replay_requested = _require_bool(replay_unchanged, "replay_unchanged")

    source_before = mutation_fingerprint(source_map)
    restored_before = mutation_fingerprint(restored_map)
    source_health = all_store_health(source_map, validated_registry)
    restored_health = all_store_health(restored_map, validated_registry)
    source_material = _reconcile_material(source_map, validated_registry, validated_scope)
    restored_material = _reconcile_material(restored_map, validated_registry, validated_scope)
    source_manifest = logical_manifest(source_map, validated_registry)
    restored_manifest = logical_manifest(restored_map, validated_registry)
    if replay_requested:
        _replay_unchanged(source_map, validated_registry, source_material)
        _replay_unchanged(restored_map, validated_registry, restored_material)
    source_after = mutation_fingerprint(source_map)
    restored_after = mutation_fingerprint(restored_map)
    if source_before["sha256"] != source_after["sha256"]:
        raise ValidationError("Stage 11 source evidence reads or replay mutated a store")
    if restored_before["sha256"] != restored_after["sha256"]:
        raise ValidationError("Stage 11 restored evidence reads or replay mutated a store")
    if source_health.to_primitive() != restored_health.to_primitive():
        raise ValidationError("Stage 11 restored health differs from its source cohort")
    if source_manifest["sha256"] != restored_manifest["sha256"]:
        raise ValidationError("Stage 11 restored logical manifest differs from its source cohort")
    if source_material.public.to_primitive() != restored_material.public.to_primitive():
        raise ValidationError("Stage 11 restored raw reconciliation differs from source")

    material = {
        "reconciliation": source_material.public.to_primitive(),
        "registry_schema_version": validated_registry.schema_version,
        "registry_version": validated_registry.registry_version,
        "registry_source_sha256": validated_registry.source_sha256,
        "source_health_sha256": _sha256_json(source_health.to_primitive()),
        "restored_health_sha256": _sha256_json(restored_health.to_primitive()),
        "source_logical_manifest_sha256": source_manifest["sha256"],
        "restored_logical_manifest_sha256": restored_manifest["sha256"],
        "source_mutation_before_sha256": source_before["sha256"],
        "source_mutation_after_sha256": source_after["sha256"],
        "restored_mutation_before_sha256": restored_before["sha256"],
        "restored_mutation_after_sha256": restored_after["sha256"],
        "source_reconciliation_sha256": source_material.public.sha256,
        "restored_reconciliation_sha256": restored_material.public.sha256,
        "source_restored_equal": True,
        "source_reads_unchanged": True,
        "restored_reads_unchanged": True,
        "raw_reparsed_equal": True,
        "complete_bea_cohorts": True,
        "complete_retail_pagination": True,
        "complete_weekly_series": True,
        "canonical_rows_match_raw": True,
        "correction_lineage_verified": True,
        "no_post_cutoff_invention": True,
        "stage3_relations_reinterpreted": False,
        "replay_unchanged": replay_requested,
        "backup_restore_outcome": "validated_restored_cohort_equal",
    }
    return Stage11RepopulationEvidence(
        reconciliation=source_material.public,
        **{name: value for name, value in material.items() if name != "reconciliation"},
        sha256=_sha256_json(
            {
                "contract": "quant_data.stage11_repopulation_evidence",
                "contract_version": "1.0.0",
                "reconciliation": material["reconciliation"],
                "registry": {
                    "schema_version": material["registry_schema_version"],
                    "registry_version": material["registry_version"],
                    "source_sha256": material["registry_source_sha256"],
                },
                **{
                    name: value
                    for name, value in material.items()
                    if name not in {"reconciliation", "registry_schema_version", "registry_version", "registry_source_sha256"}
                },
            }
        ),
    )


def _render_generated_at(value: Callable[[], object] | object) -> str:
    candidate = value() if callable(value) else value
    if isinstance(candidate, datetime):
        if candidate.tzinfo is None or candidate.utcoffset() is None:
            raise ValidationError("Stage 11 generated_at must be timezone-aware")
        return (
            candidate.astimezone(timezone.utc)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )
    return _canonical_datetime(candidate, "generated_at")


@dataclass(frozen=True, slots=True)
class Stage11CandidateReceipt:
    """Private, immutable Stage 11 candidate receipt with no promotion route."""

    attempt_id: str
    generated_at: str
    code_revision: str
    candidate_state: str
    evidence: Mapping[str, object]
    evidence_sha256: str
    receipt_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.attempt_id, str) or _ATTEMPT_ID.fullmatch(self.attempt_id) is None:
            raise ValidationError("Stage 11 attempt_id must be a safe bounded identifier")
        _canonical_datetime(self.generated_at, "generated_at")
        if not isinstance(self.code_revision, str) or _CODE_REVISION.fullmatch(self.code_revision) is None:
            raise ValidationError("Stage 11 code_revision must be a lowercase source revision")
        if self.candidate_state != _CANDIDATE_STATE:
            raise ValidationError("Stage 11 receipt cannot claim an operational promotion")
        if not isinstance(self.evidence, Mapping):
            raise ValidationError("Stage 11 candidate receipt requires path-free evidence")
        _assert_public_safe(self.evidence, "candidate evidence")
        frozen = _freeze_json(self.evidence, "candidate evidence")
        if not isinstance(frozen, Mapping):
            raise ValidationError("Stage 11 candidate receipt requires path-free evidence")
        object.__setattr__(self, "evidence", frozen)
        _require_digest(self.evidence_sha256, "evidence_sha256")
        _require_digest(self.receipt_sha256, "receipt_sha256")
        evidence = _thaw_json(frozen)
        if not isinstance(evidence, Mapping):
            raise ValidationError("Stage 11 candidate receipt requires path-free evidence")
        evidence_material = {name: value for name, value in evidence.items() if name != "sha256"}
        if evidence.get("sha256") != self.evidence_sha256 or _sha256_json(evidence_material) != self.evidence_sha256:
            raise ValidationError("Stage 11 candidate receipt evidence digest is invalid")
        if self.receipt_sha256 != _sha256_json(self.material()):
            raise ValidationError("Stage 11 candidate receipt digest is invalid")

    def material(self) -> dict[str, object]:
        return {
            "contract": "quant_data.stage11_candidate_receipt",
            "contract_version": "1.0.0",
            "attempt_id": self.attempt_id,
            "generated_at": self.generated_at,
            "code_revision": self.code_revision,
            "candidate_state": self.candidate_state,
            "evidence": _thaw_json(self.evidence),
            "evidence_sha256": self.evidence_sha256,
        }

    def to_primitive(self) -> dict[str, object]:
        return {**self.material(), "receipt_sha256": self.receipt_sha256}


class PrivateStage11CandidateState:
    """Append-only private Stage 11 candidate receipts, never promotion state."""

    def __init__(self, root: str | Path) -> None:
        if not isinstance(root, (str, Path)) or (isinstance(root, str) and not root.strip()):
            raise ValidationError("Stage 11 candidate root must be an explicit absolute directory")
        supplied = Path(root).expanduser()
        try:
            if not supplied.is_absolute() or supplied.is_symlink():
                raise OSError("candidate root is unsafe")
            candidate = supplied.resolve(strict=False)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValidationError("Stage 11 candidate root must be an explicit absolute directory") from exc
        if (
            candidate == Path(candidate.anchor)
            or candidate == Path.home().resolve(strict=False)
            or (candidate.exists() and (not candidate.is_dir() or candidate.is_symlink()))
        ):
            raise ValidationError("Stage 11 candidate root is unavailable or too broad")
        self._root = candidate

    @property
    def root(self) -> Path:
        return self._root

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        descriptor = os.open(directory, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _receipt_directory(self) -> Path:
        try:
            self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
            if not self._root.is_dir() or self._root.is_symlink():
                raise OSError("candidate root is unavailable")
            os.chmod(self._root, 0o700)
            directory = self._root / "promotion-candidates"
            directory.mkdir(mode=0o700, exist_ok=True)
            if not directory.is_dir() or directory.is_symlink():
                raise OSError("candidate directory is unavailable")
            os.chmod(directory, 0o700)
            self._fsync_directory(self._root)
        except OSError as exc:
            raise OSError("Stage 11 private candidate directory publication failed") from exc
        return directory

    def _publish_receipt(self, receipt: Stage11CandidateReceipt) -> None:
        payload = (dumps_strict(receipt.to_primitive(), max_bytes=_MAX_RECEIPT_BYTES) + "\n").encode("utf-8")
        if not payload or len(payload) > _MAX_RECEIPT_BYTES:
            raise ResourceLimitError("Stage 11 candidate receipt exceeds its bounded size")
        directory = self._receipt_directory()
        final_path = directory / f"{receipt.attempt_id}.json"
        if final_path.exists() or final_path.is_symlink():
            raise ConflictError("Stage 11 private candidate receipt already exists")
        temporary = directory / f".{receipt.attempt_id}.{uuid.uuid4().hex}.tmp"
        descriptor: int | None = None
        linked = False
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                descriptor = None
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            # link(2) is atomic and cannot overwrite an existing receipt.
            os.link(temporary, final_path)
            linked = True
            os.chmod(final_path, 0o600)
            self._fsync_directory(directory)
            os.unlink(temporary)
            self._fsync_directory(directory)
        except FileExistsError as exc:
            raise ConflictError("Stage 11 private candidate receipt already exists") from exc
        except OSError as exc:
            raise OSError("Stage 11 private candidate receipt publication failed") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary.exists() or temporary.is_symlink():
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
            if linked and not final_path.is_file():
                raise OSError("Stage 11 private candidate receipt disappeared")

    def publish(
        self,
        evidence: Stage11RepopulationEvidence,
        code_revision: str,
        now: Callable[[], object] | object,
        attempt_id: str,
    ) -> Stage11CandidateReceipt:
        """Persist exactly one durable private receipt after all safety gates."""

        if not isinstance(evidence, Stage11RepopulationEvidence):
            raise ValidationError("Stage 11 candidate requires repopulation evidence")
        if not (
            evidence.source_restored_equal
            and evidence.source_reads_unchanged
            and evidence.restored_reads_unchanged
            and evidence.raw_reparsed_equal
            and evidence.complete_macro_coverage
            and evidence.canonical_rows_match_raw
            and evidence.correction_lineage_verified
            and evidence.no_post_cutoff_invention
            and not evidence.stage3_relations_reinterpreted
            and evidence.replay_unchanged
            and evidence.backup_restore_outcome == "validated_restored_cohort_equal"
        ):
            raise ValidationError("Stage 11 candidate evidence has not passed its safety gates")
        if not isinstance(code_revision, str) or _CODE_REVISION.fullmatch(code_revision) is None:
            raise ValidationError("Stage 11 code_revision must be a lowercase source revision")
        if not isinstance(attempt_id, str) or _ATTEMPT_ID.fullmatch(attempt_id) is None:
            raise ValidationError("Stage 11 attempt_id must be a safe bounded identifier")
        generated_at = _render_generated_at(now)
        evidence_primitive = evidence.to_primitive()
        _assert_public_safe(evidence_primitive, "candidate evidence")
        if evidence.sha256 != _sha256_json(evidence.material()):
            raise ValidationError("Stage 11 repopulation evidence digest is inconsistent")
        material = {
            "contract": "quant_data.stage11_candidate_receipt",
            "contract_version": "1.0.0",
            "attempt_id": attempt_id,
            "generated_at": generated_at,
            "code_revision": code_revision,
            "candidate_state": _CANDIDATE_STATE,
            "evidence": evidence_primitive,
            "evidence_sha256": evidence.sha256,
        }
        receipt = Stage11CandidateReceipt(
            attempt_id=attempt_id,
            generated_at=generated_at,
            code_revision=code_revision,
            candidate_state=_CANDIDATE_STATE,
            evidence=evidence_primitive,
            evidence_sha256=evidence.sha256,
            receipt_sha256=_sha256_json(material),
        )
        self._publish_receipt(receipt)
        return receipt


__all__ = (
    "PrivateStage11CandidateState",
    "Stage11CandidateReceipt",
    "Stage11MacroReconciliation",
    "Stage11RepopulationEvidence",
    "build_stage11_repopulation_evidence",
    "reconcile_stage11_macro",
)
