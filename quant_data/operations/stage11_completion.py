"""Lean, query-only completion evidence for the bounded Stage 11 cohort.

The Stage 11 completion gate deliberately trusts the successfully parsed,
published provider cohort by default.  It checks only the dedicated macro
relations needed to make that claim durable: their reviewed identities,
retained complete captures, correction chains, and current projections.  It
does not copy stores, replay raw bodies, build a whole-cohort manifest, or
reconcile the preceding Stage 10 market population.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import stat
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Final

from ..errors import ConflictError, ResourceLimitError, ValidationError
from ..json_codec import dumps_strict, loads_strict
from ..macro.stage11_publication import (
    BEA_NIPA_HISTORY_DATASET_ID,
    BEA_NIPA_HISTORY_EVIDENCE_DATASET_ID,
    EIA_ELECTRICITY_RETAIL_HISTORY_DATASET_ID,
    EIA_ELECTRICITY_RETAIL_HISTORY_EVIDENCE_DATASET_ID,
    EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_DATASET_ID,
    EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_EVIDENCE_DATASET_ID,
    STAGE11_MIGRATION_ID,
    STAGE11_MIGRATION_RESOURCE,
    STAGE11_MIGRATION_SHA256,
)
from ..macro.stage11_scope import (
    REVIEWED_STAGE11_MACRO_SCOPE_SHA256,
    STAGE11_TARGET_PROFILE_ID,
    Stage11MacroScope,
)
from ..registry import Registry
from ..stores import StoreMap, StoreRole, read_connection


_CONTRACT: Final = "quant_data.stage11_completion_evidence"
_VERSION: Final = "1.0.0"
_CANDIDATE_STATE: Final = "private_candidate_only_no_operational_promotion"
_COMPLETION_POLICY: Final = "provider_trusted_targeted_constraints"
_VINTAGE_POLICY: Final = (
    "local_capture_availability_no_provider_release_timestamp_invented"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ATTEMPT_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,159}$")
_ROOT_MODE: Final = 0o700
_FILE_MODE: Final = 0o600
_MAX_RECEIPT_BYTES: Final = 512 * 1024
_DOMAIN_NAMES: Final = ("bea_nipa", "eia_retail", "eia_weekly")
_DOMAIN_FIELDS: Final = frozenset(
    {
        "capture_anomaly_count",
        "capture_count",
        "current_count",
        "current_pointer_anomaly_count",
        "duplicate_natural_key_count",
        "duplicate_version_key_count",
        "identity_anomaly_count",
        "lineage_anomaly_count",
        "supersedes_anomaly_count",
        "version_count",
        "vintage_anomaly_count",
    }
)


def _sha256_json(value: object) -> str:
    return hashlib.sha256(dumps_strict(value).encode("utf-8")).hexdigest()


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValidationError(f"Stage 11 completion {label} is invalid")
    return value


def _count(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValidationError(f"Stage 11 completion {label} is invalid")
    return value


def _frozen_domain(value: object, name: str) -> Mapping[str, int]:
    if not isinstance(value, Mapping) or set(value) != _DOMAIN_FIELDS:
        raise ValidationError("Stage 11 completion domain counts are invalid")
    frozen = {
        field: _count(value[field], f"{name} {field}")
        for field in sorted(_DOMAIN_FIELDS)
    }
    if any(frozen[field] < 1 for field in ("capture_count", "version_count", "current_count")):
        raise ValidationError("Stage 11 completion domain is not populated")
    if any(frozen[field] != 0 for field in _DOMAIN_FIELDS if field.endswith("anomaly_count")):
        raise ValidationError("Stage 11 completion domain has anomalies")
    return MappingProxyType(frozen)


@dataclass(frozen=True, slots=True)
class Stage11CompletionEvidence:
    """Path-free, targeted proof for a populated Stage 11 macro cohort."""

    registry_schema_version: str
    registry_version: str
    registry_source_sha256: str
    scope_manifest_sha256: str
    target_profile_id: str
    completion_policy: str
    vintage_policy: str
    domains: Mapping[str, Mapping[str, int]]
    integrity_check: str
    foreign_key_anomaly_count: int
    sha256: str

    def __post_init__(self) -> None:
        if (
            self.registry_schema_version != "1.7.0"
            or self.registry_version != "2.9.0"
            or self.scope_manifest_sha256 != REVIEWED_STAGE11_MACRO_SCOPE_SHA256
            or self.target_profile_id != STAGE11_TARGET_PROFILE_ID
            or self.completion_policy != _COMPLETION_POLICY
            or self.vintage_policy != _VINTAGE_POLICY
            or self.integrity_check != "ok"
        ):
            raise ValidationError("Stage 11 completion evidence binding is invalid")
        _digest(self.registry_source_sha256, "registry source pin")
        _digest(self.scope_manifest_sha256, "scope manifest pin")
        _digest(self.sha256, "evidence digest")
        if not isinstance(self.domains, Mapping) or set(self.domains) != set(_DOMAIN_NAMES):
            raise ValidationError("Stage 11 completion domain set is invalid")
        frozen = {
            name: _frozen_domain(self.domains[name], name)
            for name in _DOMAIN_NAMES
        }
        object.__setattr__(self, "domains", MappingProxyType(frozen))
        if _count(self.foreign_key_anomaly_count, "foreign key anomaly count") != 0:
            raise ValidationError("Stage 11 completion foreign key anomalies exist")
        if self.sha256 != _sha256_json(self.material()):
            raise ValidationError("Stage 11 completion evidence digest is invalid")

    def material(self) -> dict[str, object]:
        return {
            "contract": _CONTRACT,
            "contract_version": _VERSION,
            "completion_policy": self.completion_policy,
            "domains": {
                name: dict(self.domains[name]) for name in _DOMAIN_NAMES
            },
            "foreign_key_anomaly_count": self.foreign_key_anomaly_count,
            "integrity_check": self.integrity_check,
            "registry": {
                "schema_version": self.registry_schema_version,
                "source_sha256": self.registry_source_sha256,
                "version": self.registry_version,
            },
            "scope_manifest_sha256": self.scope_manifest_sha256,
            "target_profile_id": self.target_profile_id,
            "vintage_policy": self.vintage_policy,
        }

    def to_primitive(self) -> dict[str, object]:
        return {**self.material(), "sha256": self.sha256}

    @classmethod
    def from_primitive(cls, value: object) -> "Stage11CompletionEvidence":
        if not isinstance(value, Mapping) or set(value) != {
            "completion_policy",
            "contract",
            "contract_version",
            "domains",
            "foreign_key_anomaly_count",
            "integrity_check",
            "registry",
            "scope_manifest_sha256",
            "sha256",
            "target_profile_id",
            "vintage_policy",
        }:
            raise ValidationError("Stage 11 completion evidence is invalid")
        registry = value.get("registry")
        if (
            value.get("contract") != _CONTRACT
            or value.get("contract_version") != _VERSION
            or not isinstance(registry, Mapping)
            or set(registry) != {"schema_version", "source_sha256", "version"}
        ):
            raise ValidationError("Stage 11 completion evidence is invalid")
        return cls(
            registry_schema_version=registry["schema_version"],
            registry_version=registry["version"],
            registry_source_sha256=registry["source_sha256"],
            scope_manifest_sha256=value["scope_manifest_sha256"],
            target_profile_id=value["target_profile_id"],
            completion_policy=value["completion_policy"],
            vintage_policy=value["vintage_policy"],
            domains=value["domains"],
            integrity_check=value["integrity_check"],
            foreign_key_anomaly_count=value["foreign_key_anomaly_count"],
            sha256=value["sha256"],
        )


def _freeze_json(value: object) -> object:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    raise ValidationError("Stage 11 candidate evidence is invalid")


def _thaw_json(value: object) -> object:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    raise ValidationError("Stage 11 candidate evidence is invalid")


@dataclass(frozen=True, slots=True)
class Stage11CandidateReceipt:
    """Lean private receipt compatible with the established Stage 11 format."""

    attempt_id: str
    generated_at: str
    code_revision: str
    candidate_state: str
    evidence: Mapping[str, object]
    evidence_sha256: str
    receipt_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.attempt_id, str) or _ATTEMPT_ID.fullmatch(self.attempt_id) is None:
            raise ValidationError("Stage 11 candidate attempt is invalid")
        if not isinstance(self.generated_at, str):
            raise ValidationError("Stage 11 candidate time is invalid")
        try:
            parsed = datetime.strptime(self.generated_at, "%Y-%m-%dT%H:%M:%S.%fZ")
        except ValueError as exc:
            raise ValidationError("Stage 11 candidate time is invalid") from exc
        if parsed.tzinfo is not None:
            raise ValidationError("Stage 11 candidate time is invalid")
        if not isinstance(self.code_revision, str) or _SHA256.fullmatch(self.code_revision) is None:
            raise ValidationError("Stage 11 candidate revision is invalid")
        if self.candidate_state != _CANDIDATE_STATE:
            raise ValidationError("Stage 11 candidate cannot claim promotion")
        if not isinstance(self.evidence, Mapping):
            raise ValidationError("Stage 11 candidate evidence is invalid")
        evidence = Stage11CompletionEvidence.from_primitive(self.evidence)
        if evidence.sha256 != self.evidence_sha256:
            raise ValidationError("Stage 11 candidate evidence digest is invalid")
        object.__setattr__(self, "evidence", _freeze_json(evidence.to_primitive()))
        _digest(self.receipt_sha256, "candidate receipt digest")
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

def _validate_inputs(registry: object, scope: object) -> tuple[Registry, Stage11MacroScope]:
    if (
        not isinstance(registry, Registry)
        or registry.schema_version != "1.7.0"
        or registry.registry_version != "2.9.0"
        or registry.status != "validated"
    ):
        raise ValidationError("Stage 11 completion requires the reviewed registry")
    migration = next(
        (item for item in registry.migrations if item.id == STAGE11_MIGRATION_ID),
        None,
    )
    if (
        migration is None
        or migration.store != StoreRole.MACRO.value
        or migration.ordinal != 12
        or migration.resource != STAGE11_MIGRATION_RESOURCE
        or migration.sha256 != STAGE11_MIGRATION_SHA256
    ):
        raise ValidationError("Stage 11 completion migration binding is invalid")
    if (
        not isinstance(scope, Stage11MacroScope)
        or scope.manifest_sha256 != REVIEWED_STAGE11_MACRO_SCOPE_SHA256
        or _sha256_json(scope.manifest_mapping()) != REVIEWED_STAGE11_MACRO_SCOPE_SHA256
        or scope.target_profile_id != STAGE11_TARGET_PROFILE_ID
    ):
        raise ValidationError("Stage 11 completion requires the reviewed scope")
    _digest(registry.source_sha256, "registry source pin")
    return registry, scope


def _scalar(connection: sqlite3.Connection, statement: str, parameters: tuple[object, ...] = ()) -> int:
    row = connection.execute(statement, parameters).fetchone()
    if row is None:
        raise ValidationError("Stage 11 completion aggregate query is invalid")
    return _count(row[0], "aggregate count")


def _identity_rows(
    connection: sqlite3.Connection,
    statement: str,
    parameters: tuple[object, ...] = (),
) -> tuple[tuple[str, ...], ...]:
    rows = connection.execute(statement, parameters).fetchall()
    result: list[tuple[str, ...]] = []
    for row in rows:
        values = tuple(row[index] for index in range(len(row) - 1))
        count = _count(row[len(row) - 1], "identity row count")
        if count < 1 or any(not isinstance(value, str) for value in values):
            raise ValidationError("Stage 11 completion identity aggregate is invalid")
        result.append(tuple(values))
    return tuple(result)


def _identity_anomalies(
    actual: tuple[tuple[str, ...], ...], expected: tuple[tuple[str, ...], ...]
) -> int:
    return len(set(actual).symmetric_difference(expected)) + (len(actual) - len(set(actual)))


def _duplicate_count(connection: sqlite3.Connection, table: str, columns: tuple[str, ...]) -> int:
    keys = ", ".join(columns)
    return _scalar(
        connection,
        f"SELECT COUNT(*) FROM (SELECT 1 FROM {table} GROUP BY {keys} HAVING COUNT(*) > 1)",
    )


def _pointer_anomalies(
    connection: sqlite3.Connection,
    *,
    versions: str,
    current: str,
    keys: tuple[str, ...],
) -> int:
    join_keys = " AND ".join(f"later.{key}=version.{key}" for key in keys)
    current_keys = " AND ".join(f"version.{key}=pointer.{key}" for key in keys)
    return _scalar(
        connection,
        f"""
        SELECT COUNT(*)
        FROM {current} AS pointer
        LEFT JOIN {versions} AS version
          ON version.version_id=pointer.current_version_id AND {current_keys}
        LEFT JOIN {versions} AS later
          ON {join_keys} AND later.correction_sequence > version.correction_sequence
        WHERE version.version_id IS NULL OR later.version_id IS NOT NULL
        """,
    )


def _supersedes_anomalies(
    connection: sqlite3.Connection,
    *,
    versions: str,
    keys: tuple[str, ...],
) -> int:
    matches = " AND ".join(f"previous.{key}=version.{key}" for key in keys)
    return _scalar(
        connection,
        f"""
        SELECT COUNT(*)
        FROM {versions} AS version
        LEFT JOIN {versions} AS previous
          ON previous.version_id=version.supersedes_version_id
        WHERE (version.correction_sequence=1 AND version.supersedes_version_id IS NOT NULL)
           OR (
                version.correction_sequence>1
                AND (
                    previous.version_id IS NULL
                    OR NOT ({matches})
                    OR previous.correction_sequence != version.correction_sequence - 1
                )
           )
        """,
    )


def _vintage_anomalies(connection: sqlite3.Connection, versions: str) -> int:
    return _scalar(
        connection,
        f"""
        SELECT COUNT(*) FROM {versions}
        WHERE available_at != captured_at
           OR available_precision != 'datetime'
           OR captured_precision != 'datetime'
        """,
    )


def _capture_anomalies(
    connection: sqlite3.Connection,
    *,
    table: str,
    provider: str,
    endpoint_path: str,
    dataset_id: str,
    extra_predicate: str,
    extra_parameters: tuple[object, ...],
) -> int:
    return _scalar(
        connection,
        f"""
        SELECT COALESCE(SUM(CASE WHEN provider != ? OR endpoint_path != ?
             OR dataset_id != ? OR completeness != 'complete'
             OR availability_basis != 'local_capture' OR response_bytes IS NULL
             OR length(response_bytes) < 1 OR length(response_sha256) != 64
             OR length(request_scope_sha256) != 64 OR row_count < 1
             OR NOT ({extra_predicate}) THEN 1 ELSE 0 END), 0)
        FROM {table}
        """,
        (provider, endpoint_path, dataset_id, *extra_parameters),
    )


def _capture_lineage_anomalies(
    connection: sqlite3.Connection,
    *,
    versions: str,
    captures: str,
    identity_columns: tuple[str, ...],
) -> int:
    identity = " AND ".join(
        f"capture.{column}=version.{column}" for column in identity_columns
    )
    return _scalar(
        connection,
        f"""
        SELECT COUNT(*)
        FROM {versions} AS version
        LEFT JOIN {captures} AS capture
          ON capture.capture_id=version.capture_id
        WHERE capture.capture_id IS NULL
           OR capture.artifact_id != version.artifact_id
           OR capture.snapshot_id != version.snapshot_id
           OR capture.run_id != version.run_id
           OR NOT ({identity})
        """,
    )


def _domain_counts(
    connection: sqlite3.Connection,
    *,
    captures: str,
    versions: str,
    current: str,
    capture_identities: tuple[str, ...],
    version_identities: tuple[str, ...],
    current_identities: tuple[str, ...],
    natural_keys: tuple[str, ...],
    lineage_identity_columns: tuple[str, ...],
    expected_capture: tuple[tuple[str, ...], ...],
    expected_version: tuple[tuple[str, ...], ...],
    provider: str,
    endpoint_path: str,
    dataset_id: str,
    capture_extra_predicate: str,
    capture_extra_parameters: tuple[object, ...],
    vintage_versions: str,
) -> dict[str, int]:
    capture_columns = ", ".join(capture_identities)
    version_columns = ", ".join(version_identities)
    current_columns = ", ".join(current_identities)
    capture_actual = _identity_rows(
        connection,
        f"SELECT {capture_columns}, COUNT(*) FROM {captures} GROUP BY {capture_columns} ORDER BY {capture_columns}",
    )
    version_actual = _identity_rows(
        connection,
        f"SELECT {version_columns}, COUNT(*) FROM {versions} GROUP BY {version_columns} ORDER BY {version_columns}",
    )
    current_actual = _identity_rows(
        connection,
        f"""
        SELECT {", ".join("version." + column for column in current_identities)}, COUNT(*)
        FROM {current} AS pointer
        JOIN {versions} AS version ON version.version_id=pointer.current_version_id
        GROUP BY {", ".join("version." + column for column in current_identities)}
        ORDER BY {", ".join("version." + column for column in current_identities)}
        """,
    )
    keys = natural_keys
    identity_anomaly_count = (
        _identity_anomalies(capture_actual, expected_capture)
        + _identity_anomalies(version_actual, expected_version)
        + _identity_anomalies(current_actual, expected_version)
    )
    return {
        "capture_count": _scalar(connection, f"SELECT COUNT(*) FROM {captures}"),
        "version_count": _scalar(connection, f"SELECT COUNT(*) FROM {versions}"),
        "current_count": _scalar(connection, f"SELECT COUNT(*) FROM {current}"),
        "capture_anomaly_count": _capture_anomalies(
            connection,
            table=captures,
            provider=provider,
            endpoint_path=endpoint_path,
            dataset_id=dataset_id,
            extra_predicate=capture_extra_predicate,
            extra_parameters=capture_extra_parameters,
        ),
        "identity_anomaly_count": identity_anomaly_count,
        "duplicate_natural_key_count": _duplicate_count(connection, current, keys),
        "duplicate_version_key_count": _duplicate_count(
            connection, versions, (*keys, "correction_sequence")
        ),
        "current_pointer_anomaly_count": _pointer_anomalies(
            connection, versions=versions, current=current, keys=keys
        ),
        "supersedes_anomaly_count": _supersedes_anomalies(
            connection, versions=versions, keys=keys
        ),
        "lineage_anomaly_count": _capture_lineage_anomalies(
            connection,
            versions=versions,
            captures=captures,
            identity_columns=lineage_identity_columns,
        ),
        "vintage_anomaly_count": _vintage_anomalies(connection, vintage_versions),
    }


def build_stage11_completion_evidence(
    store_map: StoreMap,
    registry: Registry,
    scope: Stage11MacroScope,
) -> Stage11CompletionEvidence:
    """Build the bounded query-only completion proof for exact Stage 11 facts."""

    registry, scope = _validate_inputs(registry, scope)
    if not isinstance(store_map, StoreMap):
        raise ValidationError("Stage 11 completion requires an explicit store map")
    expected_bea = tuple(
        sorted(
            (
                series.canonical_series_id,
                request.table_name,
                series.provider_series_code,
            )
            for request in scope.bea.requests
            for series in request.series
        )
    )
    expected_bea_capture = tuple((item[1], item[2]) for item in expected_bea)
    state_ids = tuple(scope.eia.retail.facets.get("stateid", ()))
    sector_ids = tuple(scope.eia.retail.facets.get("sectorid", ()))
    expected_retail = tuple(
        sorted(
            (item.canonical_series_id, item.field, state_id, sector_id)
            for item in scope.eia.retail.data
            for state_id in state_ids
            for sector_id in sector_ids
        )
    )
    expected_retail_capture = tuple((state_id, sector_id) for state_id in state_ids for sector_id in sector_ids)
    expected_weekly = ((scope.eia.weekly.canonical_series_id, scope.eia.weekly.provider_series_id),)
    if (
        len(expected_bea) != 2
        or len(expected_retail) != 4
        or len(expected_weekly) != 1
        or not expected_retail_capture
    ):
        raise ValidationError("Stage 11 completion scope identities are invalid")
    try:
        with read_connection(store_map, StoreRole.MACRO) as connection:
            quick_check_rows = connection.execute("PRAGMA quick_check").fetchall()
            if tuple(row[0] for row in quick_check_rows) != ("ok",):
                raise ValidationError("Stage 11 completion integrity check failed")
            foreign_keys = _scalar(
                connection, "SELECT COUNT(*) FROM pragma_foreign_key_check"
            )
            domains = {
                "bea_nipa": _domain_counts(
                    connection,
                    captures="stage11_bea_nipa_captures",
                    versions="stage11_bea_nipa_observation_versions",
                    current="stage11_bea_nipa_observations",
                    capture_identities=("table_name", "series_code"),
                    version_identities=("canonical_series_id", "table_name", "series_code"),
                    current_identities=("canonical_series_id", "table_name", "series_code"),
                    natural_keys=("canonical_series_id", "period"),
                    lineage_identity_columns=("table_name", "series_code"),
                    expected_capture=expected_bea_capture,
                    expected_version=expected_bea,
                    provider="bea",
                    endpoint_path=scope.bea.requests[0].path,
                    dataset_id=BEA_NIPA_HISTORY_EVIDENCE_DATASET_ID,
                    capture_extra_predicate="((table_name=? AND series_code=?) OR (table_name=? AND series_code=?))",
                    capture_extra_parameters=(
                        expected_bea_capture[0][0], expected_bea_capture[0][1],
                        expected_bea_capture[1][0], expected_bea_capture[1][1],
                    ),
                    vintage_versions="stage11_bea_nipa_observation_versions",
                ),
                "eia_retail": _domain_counts(
                    connection,
                    captures="stage11_eia_retail_captures",
                    versions="stage11_eia_retail_observation_versions",
                    current="stage11_eia_retail_observations",
                    capture_identities=("state_id", "sector_id"),
                    version_identities=("canonical_series_id", "metric", "state_id", "sector_id"),
                    current_identities=("canonical_series_id", "metric", "state_id", "sector_id"),
                    natural_keys=("canonical_series_id", "period", "state_id", "sector_id"),
                    lineage_identity_columns=("state_id", "sector_id"),
                    expected_capture=expected_retail_capture,
                    expected_version=expected_retail,
                    provider="eia",
                    endpoint_path=scope.eia.retail.path,
                    dataset_id=EIA_ELECTRICITY_RETAIL_HISTORY_EVIDENCE_DATASET_ID,
                    capture_extra_predicate="state_id=? AND sector_id=? AND page_length=?",
                    capture_extra_parameters=(state_ids[0], sector_ids[0], scope.eia.retail.length),
                    vintage_versions="stage11_eia_retail_observation_versions",
                ),
                "eia_weekly": _domain_counts(
                    connection,
                    captures="stage11_eia_weekly_captures",
                    versions="stage11_eia_weekly_observation_versions",
                    current="stage11_eia_weekly_observations",
                    capture_identities=("provider_series_id",),
                    version_identities=("canonical_series_id", "provider_series_id"),
                    current_identities=("canonical_series_id", "provider_series_id"),
                    natural_keys=("canonical_series_id", "period"),
                    lineage_identity_columns=("provider_series_id",),
                    expected_capture=((scope.eia.weekly.provider_series_id,),),
                    expected_version=expected_weekly,
                    provider="eia",
                    endpoint_path=scope.eia.weekly.path,
                    dataset_id=EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_EVIDENCE_DATASET_ID,
                    capture_extra_predicate="provider_series_id=?",
                    capture_extra_parameters=(scope.eia.weekly.provider_series_id,),
                    vintage_versions="stage11_eia_weekly_observation_versions",
                ),
            }
    except sqlite3.Error as exc:
        raise ValidationError("Stage 11 completion aggregates are unavailable") from exc
    material = {
        "registry_schema_version": registry.schema_version,
        "registry_version": registry.registry_version,
        "registry_source_sha256": registry.source_sha256,
        "scope_manifest_sha256": scope.manifest_sha256,
        "target_profile_id": scope.target_profile_id,
        "completion_policy": _COMPLETION_POLICY,
        "vintage_policy": _VINTAGE_POLICY,
        "domains": domains,
        "integrity_check": "ok",
        "foreign_key_anomaly_count": foreign_keys,
    }
    return Stage11CompletionEvidence(
        **material,
        sha256=_sha256_json(
            {
                "contract": _CONTRACT,
                "contract_version": _VERSION,
                "completion_policy": material["completion_policy"],
                "domains": material["domains"],
                "foreign_key_anomaly_count": material["foreign_key_anomaly_count"],
                "integrity_check": material["integrity_check"],
                "registry": {
                    "schema_version": material["registry_schema_version"],
                    "source_sha256": material["registry_source_sha256"],
                    "version": material["registry_version"],
                },
                "scope_manifest_sha256": material["scope_manifest_sha256"],
                "target_profile_id": material["target_profile_id"],
                "vintage_policy": material["vintage_policy"],
            }
        ),
    )


def _generated_at(value: Callable[[], object] | object) -> str:
    candidate = value() if callable(value) else value
    if not isinstance(candidate, datetime) or candidate.tzinfo is None or candidate.utcoffset() is None:
        raise ValidationError("Stage 11 completion generated_at must be timezone-aware")
    return candidate.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


class PrivateStage11CompletionCandidateState:
    """Append exactly one 0700/0600 immutable lean-completion receipt."""

    def __init__(self, root: str | Path) -> None:
        if not isinstance(root, (str, Path)) or (isinstance(root, str) and not root.strip()):
            raise ValidationError("Stage 11 completion candidate root is invalid")
        supplied = Path(root)
        try:
            if not supplied.is_absolute() or supplied.is_symlink():
                raise OSError("unsafe candidate root")
            resolved = supplied.resolve(strict=False)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValidationError("Stage 11 completion candidate root is invalid") from exc
        if resolved == Path(resolved.anchor) or resolved == Path.home().resolve(strict=False):
            raise ValidationError("Stage 11 completion candidate root is too broad")
        self._root = resolved

    @property
    def root(self) -> Path:
        return self._root

    @staticmethod
    def _fsync(directory: Path) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(directory, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _directory(path: Path, label: str) -> None:
        try:
            info = path.lstat()
        except OSError as exc:
            raise OSError(f"Stage 11 completion {label} is unavailable") from exc
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or stat.S_IMODE(info.st_mode) != _ROOT_MODE
        ):
            raise OSError(f"Stage 11 completion {label} is unsafe")

    def _receipt_directory(self) -> Path:
        try:
            self._root.mkdir(mode=_ROOT_MODE, parents=True)
        except FileExistsError:
            pass
        self._directory(self._root, "candidate root")
        directory = self._root / "promotion-candidates"
        try:
            directory.mkdir(mode=_ROOT_MODE)
        except FileExistsError:
            pass
        self._directory(directory, "candidate receipt directory")
        self._fsync(self._root)
        return directory

    def publish(
        self,
        evidence: Stage11CompletionEvidence,
        code_revision: str,
        now: Callable[[], object] | object,
        attempt_id: str,
    ) -> Stage11CandidateReceipt:
        if not isinstance(evidence, Stage11CompletionEvidence):
            raise ValidationError("Stage 11 completion candidate evidence is invalid")
        if not isinstance(code_revision, str) or _SHA256.fullmatch(code_revision) is None:
            raise ValidationError("Stage 11 completion candidate revision is invalid")
        if not isinstance(attempt_id, str) or _ATTEMPT_ID.fullmatch(attempt_id) is None:
            raise ValidationError("Stage 11 completion candidate attempt is invalid")
        evidence_primitive = evidence.to_primitive()
        generated_at = _generated_at(now)
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
        payload = dumps_strict(receipt.to_primitive(), max_bytes=_MAX_RECEIPT_BYTES).encode("utf-8")
        directory = self._receipt_directory()
        final_path = directory / f"{attempt_id}.json"
        if final_path.exists() or final_path.is_symlink():
            raise ConflictError("Stage 11 completion candidate receipt already exists")
        temporary = directory / f".{attempt_id}.{uuid.uuid4().hex}.tmp"
        descriptor: int | None = None
        linked = False
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(temporary, flags, _FILE_MODE)
            offset = 0
            while offset < len(payload):
                written = os.write(descriptor, payload[offset:])
                if written <= 0:
                    raise OSError("Stage 11 completion candidate write failed")
                offset += written
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            os.link(temporary, final_path)
            linked = True
            os.chmod(final_path, _FILE_MODE)
            self._fsync(directory)
            temporary.unlink()
            self._fsync(directory)
        except FileExistsError as exc:
            raise ConflictError("Stage 11 completion candidate receipt already exists") from exc
        except OSError as exc:
            raise OSError("Stage 11 completion candidate receipt publication failed") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary.exists() or temporary.is_symlink():
                try:
                    temporary.unlink()
                except OSError:
                    pass
        try:
            info = final_path.lstat()
        except OSError as exc:
            raise OSError("Stage 11 completion candidate receipt disappeared") from exc
        if (
            not linked
            or not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or stat.S_IMODE(info.st_mode) != _FILE_MODE
            or info.st_nlink != 1
        ):
            raise OSError("Stage 11 completion candidate receipt is unsafe")
        return receipt


__all__ = (
    "PrivateStage11CompletionCandidateState",
    "Stage11CandidateReceipt",
    "Stage11CompletionEvidence",
    "build_stage11_completion_evidence",
)
