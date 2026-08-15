"""Offline-safe publication for the bounded Stage 11 BEA/EIA history cohort.

Capture and parsing live in :mod:`stage11_bea` and :mod:`stage11_eia`.  This
module accepts only their complete, credential-free capture objects, prepares
an opaque candidate before any write lock is acquired, then publishes it in a
single short macro-store transaction through :class:`IngestionCoordinator`.

The Stage 11 sources have local-capture availability only.  In particular,
this code does not infer a BEA vintage, a first release, or a provider
publication instant from a source period or response metadata.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import weakref
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Mapping

from ..contracts import IngestionReceipt
from ..errors import ConflictError, ValidationError
from ..ingestion import (
    ArtifactWrite,
    IngestionCoordinator,
    QualityWrite,
    SnapshotWrite,
    WriteResult,
)
from ..json_codec import dumps_strict
from ..registry import Registry
from ..stores import (
    HeldWriteLocks,
    StoreMap,
    StoreRole,
    canonical_path_uri,
    stable_id,
)
from .stage11_bea import (
    BEA_AVAILABILITY_BASIS,
    BeaNipaCapture,
    BeaNipaHistoryCohort,
    BeaNipaObservation,
)
from .stage11_eia import (
    EIA_RETAIL_PAGE_LENGTH,
    EIA_WEEKLY_CANONICAL_SERIES_ID,
    EIA_WEEKLY_SERIES_ID,
    EiaRetailCapture,
    EiaRetailPageCapture,
    EiaRetailObservation,
    EiaWeeklyCapture,
    EiaWeeklyObservation,
)


BEA_NIPA_HISTORY_COLLECTOR_ID = "bea.macro.stage11_nipa_history"
EIA_ELECTRICITY_RETAIL_HISTORY_COLLECTOR_ID = (
    "eia.macro.stage11_electricity_retail_history"
)
EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_COLLECTOR_ID = (
    "eia.macro.stage11_petroleum_weekly_stock_history"
)

BEA_NIPA_HISTORY_EVIDENCE_DATASET_ID = "macro.bea.nipa_history_evidence"
BEA_NIPA_HISTORY_DATASET_ID = "macro.bea.nipa_history"
EIA_ELECTRICITY_RETAIL_HISTORY_EVIDENCE_DATASET_ID = (
    "macro.eia.electricity_retail_history_evidence"
)
EIA_ELECTRICITY_RETAIL_HISTORY_DATASET_ID = "macro.eia.electricity_retail_history"
EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_EVIDENCE_DATASET_ID = (
    "macro.eia.petroleum_weekly_stock_history_evidence"
)
EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_DATASET_ID = (
    "macro.eia.petroleum_weekly_stock_history"
)

STAGE11_MIGRATION_ID = "macro:0012_stage11_bea_eia_live_history"
STAGE11_MIGRATION_RESOURCE = (
    "quant_data/migrations/macro/0012_stage11_bea_eia_live_history.sql"
)
STAGE11_MIGRATION_SHA256 = (
    "4f29eed2d73fcb1aa6f3c519a3360c132658cf152341261a18d4332e140fd0a5"
)

BEA_NIPA_NORMALIZATION_VERSION = "stage11.bea.nipa.v1"
EIA_RETAIL_NORMALIZATION_VERSION = "stage11.eia.retail.v1"
EIA_WEEKLY_NORMALIZATION_VERSION = "stage11.eia.weekly.v1"

BEA_NIPA_RELATIONS = (
    "stage11_bea_nipa_captures",
    "stage11_bea_nipa_observation_versions",
    "stage11_bea_nipa_observations",
)
EIA_RETAIL_RELATIONS = (
    "stage11_eia_retail_captures",
    "stage11_eia_retail_observation_versions",
    "stage11_eia_retail_observations",
)
EIA_WEEKLY_RELATIONS = (
    "stage11_eia_weekly_captures",
    "stage11_eia_weekly_observation_versions",
    "stage11_eia_weekly_observations",
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
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
    b"\"headers\"",
    b"\"header\"",
    b"\"query\"",
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


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: object) -> str:
    return _sha256(dumps_strict(value).encode("utf-8"))


def _require_digest(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValidationError(f"Stage 11 {field_name} is invalid")
    return value


def _utc_text(value: object, field_name: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError(f"Stage 11 {field_name} must be an aware datetime")
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _decimal_text(value: Decimal) -> str:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValidationError("Stage 11 observation value must be finite")
    if value.is_zero():
        return "0"
    return format(value.normalize(), "f")


def _store_map_identity(store_map: StoreMap) -> tuple[tuple[str, str], ...]:
    if not isinstance(store_map, StoreMap):
        raise ValidationError("Stage 11 importer requires explicit stores")
    return tuple(
        (role.value, canonical_path_uri(path)) for role, path in store_map.items()
    )


def _assert_sanitized_raw_bytes(value: object) -> bytes:
    """Defence in depth for the capture-layer credential sanitization boundary."""

    if not isinstance(value, bytes) or not value:
        raise ValidationError("Stage 11 retained response bytes are invalid")
    lowered = value.lower()
    if any(token in lowered for token in _FORBIDDEN_EVIDENCE_TOKENS):
        raise ValidationError("Stage 11 retained response contains unsafe request material")
    return value


def _validate_registry(registry: object) -> Registry:
    if (
        not isinstance(registry, Registry)
        or registry.schema_version != "1.7.0"
        or registry.registry_version != "2.9.0"
        or registry.status != "validated"
    ):
        raise ValidationError("Stage 11 importer requires the reviewed 1.7.0/2.9.0 registry")
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
        or migration.reconstruction_state != "fixture_validated"
    ):
        raise ValidationError("Stage 11 migration declaration is invalid")

    expected_datasets = {
        BEA_NIPA_HISTORY_EVIDENCE_DATASET_ID: ("evidence", (BEA_NIPA_RELATIONS[0],)),
        BEA_NIPA_HISTORY_DATASET_ID: ("canonical", BEA_NIPA_RELATIONS[1:]),
        EIA_ELECTRICITY_RETAIL_HISTORY_EVIDENCE_DATASET_ID: (
            "evidence",
            (EIA_RETAIL_RELATIONS[0],),
        ),
        EIA_ELECTRICITY_RETAIL_HISTORY_DATASET_ID: (
            "canonical",
            EIA_RETAIL_RELATIONS[1:],
        ),
        EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_EVIDENCE_DATASET_ID: (
            "evidence",
            (EIA_WEEKLY_RELATIONS[0],),
        ),
        EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_DATASET_ID: (
            "canonical",
            EIA_WEEKLY_RELATIONS[1:],
        ),
    }
    datasets = {item.id: item for item in registry.datasets}
    for dataset_id, (layer, relations) in expected_datasets.items():
        declaration = datasets.get(dataset_id)
        if (
            declaration is None
            or declaration.store != StoreRole.MACRO.value
            or declaration.layer != layer
            or not declaration.active
            or tuple(declaration.relations) != relations
            or declaration.tool_ids
            or declaration.dashboard_ids
            or declaration.export_ids
        ):
            raise ValidationError("Stage 11 dataset declaration is invalid")

    expected_collectors = {
        BEA_NIPA_HISTORY_COLLECTOR_ID: _BEA_OUTPUT_DATASETS,
        EIA_ELECTRICITY_RETAIL_HISTORY_COLLECTOR_ID: _EIA_RETAIL_OUTPUT_DATASETS,
        EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_COLLECTOR_ID: _EIA_WEEKLY_OUTPUT_DATASETS,
    }
    collectors = {str(item.get("id")): item for item in registry.collectors}
    for collector_id, outputs in expected_collectors.items():
        collector = collectors.get(collector_id)
        if (
            collector is None
            or collector.get("network") is not True
            or tuple(collector.get("output_datasets", ())) != outputs
            or collector.get("schedule_eligibility") != {"mode": "manual_only"}
            or collector.get("mutation_policy")
            != {
                "mode": "append_versions_and_move_current_projection",
                "unchanged": "zero_persistent_writes",
            }
        ):
            raise ValidationError("Stage 11 collector declaration is invalid")
    return registry


@dataclass(frozen=True, slots=True)
class _BeaPublication:
    cohort: BeaNipaHistoryCohort
    request_scope: Mapping[str, object]
    semantic_identity: str
    captured_at: str
    run_id: str
    snapshot_id: str


@dataclass(frozen=True, slots=True)
class _RetailPublication:
    cohort: EiaRetailCapture
    request_scope: Mapping[str, object]
    semantic_identity: str
    captured_at: str
    run_id: str
    snapshot_id: str


@dataclass(frozen=True, slots=True)
class _WeeklyPublication:
    capture: EiaWeeklyCapture
    request_scope: Mapping[str, object]
    semantic_identity: str
    captured_at: str
    run_id: str
    snapshot_id: str


@dataclass(frozen=True, slots=True)
class _PreparedState:
    owner_token: object
    store_map: StoreMap
    registry: Registry
    store_map_identity: tuple[tuple[str, str], ...]
    bea: _BeaPublication | None
    retail: _RetailPublication | None
    weekly: _WeeklyPublication | None


@dataclass(frozen=True, slots=True)
class _CaptureBinding:
    capture_id: str
    artifact_id: str
    snapshot_id: str
    inserted: bool


class PreparedStage11MacroPublication:
    """Opaque importer-bound candidate with raw evidence hidden from ``repr``."""

    __slots__ = ("__weakref__",)

    def __init__(self) -> None:
        raise TypeError("Prepared Stage 11 candidates are created by an importer")

    def __init_subclass__(cls, **kwargs: object) -> None:
        del cls, kwargs
        raise TypeError("Prepared Stage 11 candidates cannot be subclassed")


_PREPARED: "weakref.WeakKeyDictionary[PreparedStage11MacroPublication, _PreparedState]" = (
    weakref.WeakKeyDictionary()
)


def _new_prepared(state: _PreparedState) -> PreparedStage11MacroPublication:
    candidate = object.__new__(PreparedStage11MacroPublication)
    _PREPARED[candidate] = state
    return candidate


def _capture_artifact(
    *,
    artifact_id: str,
    dataset_id: str,
    response_sha256: str,
    response_bytes: bytes,
    request_scope: Mapping[str, object],
    captured_at: str,
    normalization_version: str,
    source_reference: str,
) -> ArtifactWrite:
    return ArtifactWrite(
        artifact_id=artifact_id,
        dataset_id=dataset_id,
        content_sha256=response_sha256,
        media_type="application/json",
        byte_count=len(response_bytes),
        source_reference=source_reference,
        request_scope=dict(request_scope),
        captured_at=captured_at,
        captured_precision="datetime",
        normalization_version=normalization_version,
    )


def _quality_results(
    *,
    prefix: str,
    semantic_identity: str,
    snapshot_id: str,
    evidence_dataset_id: str,
    canonical_dataset_id: str,
    artifact_id: str,
    observed: Mapping[str, object],
    canonical_rule: str,
) -> tuple[QualityWrite, QualityWrite]:
    return (
        QualityWrite(
            quality_result_id=stable_id(prefix, evidence_dataset_id, semantic_identity),
            dataset_id=evidence_dataset_id,
            rule_id="sanitized_complete_capture",
            rule_version="1.0.0",
            severity="critical",
            outcome="passed",
            subject_kind="snapshot",
            subject_id=snapshot_id,
            artifact_id=artifact_id,
            snapshot_id=snapshot_id,
            observed=dict(observed),
        ),
        QualityWrite(
            quality_result_id=stable_id(prefix, canonical_dataset_id, semantic_identity),
            dataset_id=canonical_dataset_id,
            rule_id=canonical_rule,
            rule_version="1.0.0",
            severity="critical",
            outcome="passed",
            subject_kind="snapshot",
            subject_id=snapshot_id,
            snapshot_id=snapshot_id,
            observed=dict(observed),
        ),
    )


class Stage11MacroImporter:
    """Publish complete Stage 11 captures without transport or credential inputs."""

    def __init__(
        self,
        store_map: StoreMap,
        registry: Registry,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if not isinstance(store_map, StoreMap) or not callable(clock):
            raise ValidationError("Stage 11 importer requires explicit stores and a clock")
        self.store_map = store_map
        self.registry = _validate_registry(registry)
        self._clock = clock
        self._coordinator = IngestionCoordinator(store_map, code_version="stage11.0.0")
        self._owner_token = object()

    def _prepared_state(self, prepared: object) -> _PreparedState:
        if not isinstance(prepared, PreparedStage11MacroPublication):
            raise ValidationError("Stage 11 publication requires a prepared candidate")
        state = _PREPARED.get(prepared)
        kinds = (state.bea, state.retail, state.weekly) if state is not None else ()
        if (
            state is None
            or state.owner_token is not self._owner_token
            or state.store_map is not self.store_map
            or state.registry is not self.registry
            or state.store_map_identity != _store_map_identity(self.store_map)
            or sum(item is not None for item in kinds) != 1
        ):
            raise ValidationError("Stage 11 prepared candidate is foreign or expired")
        return state

    def _candidate(
        self,
        *,
        bea: _BeaPublication | None = None,
        retail: _RetailPublication | None = None,
        weekly: _WeeklyPublication | None = None,
    ) -> PreparedStage11MacroPublication:
        if sum(item is not None for item in (bea, retail, weekly)) != 1:
            raise ValidationError("Stage 11 prepared candidate kind is invalid")
        return _new_prepared(
            _PreparedState(
                owner_token=self._owner_token,
                store_map=self.store_map,
                registry=self.registry,
                store_map_identity=_store_map_identity(self.store_map),
                bea=bea,
                retail=retail,
                weekly=weekly,
            )
        )

    def prepare_bea_nipa_history(
        self, cohort: BeaNipaHistoryCohort
    ) -> PreparedStage11MacroPublication:
        if not isinstance(cohort, BeaNipaHistoryCohort):
            raise ValidationError("Stage 11 BEA publication requires a complete NIPA cohort")
        if cohort.availability_basis != BEA_AVAILABILITY_BASIS:
            raise ValidationError("Stage 11 BEA availability must be local capture")
        for capture in cohort.captures:
            _assert_sanitized_raw_bytes(capture.raw_bytes)
            _require_digest(capture.raw_bytes_sha256, "BEA response digest")
        captured_at = _utc_text(self._clock(), "captured_at")
        request_scope: dict[str, object] = {
            "provider": "bea",
            "availability_basis": "local_capture",
            "requests": [capture.request_scope for capture in cohort.captures],
        }
        semantic_identity = _sha256_json(
            {
                "collector_id": BEA_NIPA_HISTORY_COLLECTOR_ID,
                "canonical_dataset_id": BEA_NIPA_HISTORY_DATASET_ID,
                "request_scope": request_scope,
                "normalization_version": BEA_NIPA_NORMALIZATION_VERSION,
                "normalized_complete_history_sha256": cohort.semantic_sha256,
            }
        )
        _require_digest(semantic_identity, "BEA semantic identity")
        return self._candidate(
            bea=_BeaPublication(
                cohort=cohort,
                request_scope=request_scope,
                semantic_identity=semantic_identity,
                captured_at=captured_at,
                run_id=stable_id(
                    "stage11_bea_nipa_run", BEA_NIPA_HISTORY_DATASET_ID, semantic_identity
                ),
                snapshot_id=stable_id(
                    "stage11_bea_nipa_snapshot",
                    BEA_NIPA_HISTORY_DATASET_ID,
                    semantic_identity,
                ),
            )
        )

    def prepare_eia_retail_history(
        self, cohort: EiaRetailCapture
    ) -> PreparedStage11MacroPublication:
        if (
            not isinstance(cohort, EiaRetailCapture)
            or cohort.completeness != "complete"
            or cohort.tombstone_authoritative is not False
        ):
            raise ValidationError("Stage 11 EIA retail publication requires complete non-tombstone pages")
        for page in cohort.pages:
            _assert_sanitized_raw_bytes(page.raw_bytes)
            _require_digest(page.raw_bytes_sha256, "EIA retail response digest")
        captured_at = _utc_text(self._clock(), "captured_at")
        request_scope = dict(cohort.request_scope)
        request_scope["availability_basis"] = "local_capture"
        semantic_identity = _sha256_json(
            {
                "collector_id": EIA_ELECTRICITY_RETAIL_HISTORY_COLLECTOR_ID,
                "canonical_dataset_id": EIA_ELECTRICITY_RETAIL_HISTORY_DATASET_ID,
                "request_scope": request_scope,
                "normalization_version": EIA_RETAIL_NORMALIZATION_VERSION,
                "normalized_complete_history_sha256": cohort.semantic_sha256,
            }
        )
        _require_digest(semantic_identity, "EIA retail semantic identity")
        return self._candidate(
            retail=_RetailPublication(
                cohort=cohort,
                request_scope=request_scope,
                semantic_identity=semantic_identity,
                captured_at=captured_at,
                run_id=stable_id(
                    "stage11_eia_retail_run",
                    EIA_ELECTRICITY_RETAIL_HISTORY_DATASET_ID,
                    semantic_identity,
                ),
                snapshot_id=stable_id(
                    "stage11_eia_retail_snapshot",
                    EIA_ELECTRICITY_RETAIL_HISTORY_DATASET_ID,
                    semantic_identity,
                ),
            )
        )

    def prepare_eia_weekly_history(
        self, capture: EiaWeeklyCapture
    ) -> PreparedStage11MacroPublication:
        if not isinstance(capture, EiaWeeklyCapture):
            raise ValidationError("Stage 11 EIA weekly publication requires a complete capture")
        _assert_sanitized_raw_bytes(capture.raw_bytes)
        _require_digest(capture.raw_bytes_sha256, "EIA weekly response digest")
        captured_at = _utc_text(self._clock(), "captured_at")
        request_scope = dict(capture.request_scope)
        request_scope["availability_basis"] = "local_capture"
        semantic_identity = _sha256_json(
            {
                "collector_id": EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_COLLECTOR_ID,
                "canonical_dataset_id": EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_DATASET_ID,
                "request_scope": request_scope,
                "normalization_version": EIA_WEEKLY_NORMALIZATION_VERSION,
                "normalized_complete_history_sha256": capture.semantic_sha256,
            }
        )
        _require_digest(semantic_identity, "EIA weekly semantic identity")
        return self._candidate(
            weekly=_WeeklyPublication(
                capture=capture,
                request_scope=request_scope,
                semantic_identity=semantic_identity,
                captured_at=captured_at,
                run_id=stable_id(
                    "stage11_eia_weekly_run",
                    EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_DATASET_ID,
                    semantic_identity,
                ),
                snapshot_id=stable_id(
                    "stage11_eia_weekly_snapshot",
                    EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_DATASET_ID,
                    semantic_identity,
                ),
            )
        )

    def publish_prepared(
        self,
        prepared: PreparedStage11MacroPublication,
        *,
        held_locks: HeldWriteLocks | None = None,
    ) -> IngestionReceipt:
        """Publish one prepared candidate; no parsing or network work occurs here."""

        state = self._prepared_state(prepared)
        if held_locks is not None:
            if not isinstance(held_locks, HeldWriteLocks):
                raise ValidationError("Held write-lock capability is invalid")
            held_locks._require_target(self.store_map, StoreRole.MACRO)
        if state.bea is not None:
            return self._publish_bea(state.bea, held_locks=held_locks)
        if state.retail is not None:
            return self._publish_retail(state.retail, held_locks=held_locks)
        assert state.weekly is not None
        return self._publish_weekly(state.weekly, held_locks=held_locks)

    @staticmethod
    def _existing_bea_capture(
        connection: sqlite3.Connection, capture: BeaNipaCapture
    ) -> _CaptureBinding | None:
        row = connection.execute(
            """
            SELECT capture_id, artifact_id, snapshot_id
            FROM stage11_bea_nipa_captures
            WHERE table_name=? AND series_code=? AND response_sha256=?
            """,
            (capture.request.table_name, capture.request.series_code, capture.raw_bytes_sha256),
        ).fetchone()
        if row is None:
            return None
        return _CaptureBinding(str(row["capture_id"]), str(row["artifact_id"]), str(row["snapshot_id"]), False)

    @staticmethod
    def _existing_retail_capture(
        connection: sqlite3.Connection, page: EiaRetailPageCapture
    ) -> _CaptureBinding | None:
        row = connection.execute(
            """
            SELECT capture_id, artifact_id, snapshot_id
            FROM stage11_eia_retail_captures
            WHERE page_offset=? AND response_sha256=?
            """,
            (page.request.offset, page.raw_bytes_sha256),
        ).fetchone()
        if row is None:
            return None
        return _CaptureBinding(str(row["capture_id"]), str(row["artifact_id"]), str(row["snapshot_id"]), False)

    @staticmethod
    def _existing_weekly_capture(
        connection: sqlite3.Connection, capture: EiaWeeklyCapture
    ) -> _CaptureBinding | None:
        row = connection.execute(
            """
            SELECT capture_id, artifact_id, snapshot_id
            FROM stage11_eia_weekly_captures
            WHERE provider_series_id=? AND response_sha256=?
            """,
            (EIA_WEEKLY_SERIES_ID, capture.raw_bytes_sha256),
        ).fetchone()
        if row is None:
            return None
        return _CaptureBinding(str(row["capture_id"]), str(row["artifact_id"]), str(row["snapshot_id"]), False)

    def _publish_bea(
        self, publication: _BeaPublication, *, held_locks: HeldWriteLocks | None
    ) -> IngestionReceipt:
        def writer(connection: sqlite3.Connection, active_run_id: str) -> WriteResult:
            if active_run_id != publication.run_id:
                raise ValidationError("Ingestion coordinator supplied an unexpected run identity")
            bindings: dict[tuple[str, str], _CaptureBinding] = {}
            artifacts: list[ArtifactWrite] = []
            written = 0
            for capture in publication.cohort.captures:
                pair = (capture.request.table_name, capture.request.series_code)
                binding = self._existing_bea_capture(connection, capture)
                if binding is None:
                    scope = capture.request_scope
                    scope_sha256 = _sha256_json(scope)
                    capture_semantic = _sha256_json(
                        {
                            "publication_semantic_identity": publication.semantic_identity,
                            "request_scope": scope,
                            "response_sha256": capture.raw_bytes_sha256,
                            "normalized_history_sha256": capture.semantic_sha256,
                        }
                    )
                    capture_id = stable_id("stage11_bea_nipa_capture", capture_semantic)
                    artifact_id = stable_id(
                        "stage11_bea_nipa_artifact",
                        BEA_NIPA_HISTORY_EVIDENCE_DATASET_ID,
                        capture.raw_bytes_sha256,
                        scope_sha256,
                    )
                    capture_snapshot_id = stable_id(
                        "stage11_bea_nipa_capture_snapshot", capture_semantic
                    )
                    connection.execute(
                        """
                        INSERT INTO stage11_bea_nipa_captures (
                            capture_id, dataset_id, provider, endpoint_path, table_name,
                            series_code, request_scope_json, request_scope_sha256,
                            response_sha256, response_bytes, semantic_identity, completeness,
                            availability_basis, artifact_id, snapshot_id, captured_at,
                            captured_precision, row_count, normalization_version, run_id
                        ) VALUES (?, ?, 'bea', ?, ?, ?, ?, ?, ?, ?, ?, 'complete',
                                  'local_capture', ?, ?, ?, 'datetime', ?, ?, ?)
                        """,
                        (
                            capture_id,
                            BEA_NIPA_HISTORY_EVIDENCE_DATASET_ID,
                            capture.request.endpoint_path,
                            capture.request.table_name,
                            capture.request.series_code,
                            dumps_strict(scope),
                            scope_sha256,
                            capture.raw_bytes_sha256,
                            capture.raw_bytes,
                            capture_semantic,
                            artifact_id,
                            capture_snapshot_id,
                            publication.captured_at,
                            len(capture.rows),
                            BEA_NIPA_NORMALIZATION_VERSION,
                            active_run_id,
                        ),
                    )
                    binding = _CaptureBinding(capture_id, artifact_id, capture_snapshot_id, True)
                    artifacts.append(
                        _capture_artifact(
                            artifact_id=artifact_id,
                            dataset_id=BEA_NIPA_HISTORY_EVIDENCE_DATASET_ID,
                            response_sha256=capture.raw_bytes_sha256,
                            response_bytes=capture.raw_bytes,
                            request_scope=scope,
                            captured_at=publication.captured_at,
                            normalization_version=BEA_NIPA_NORMALIZATION_VERSION,
                            source_reference="bea/nipa_history",
                        )
                    )
                    written += 1
                bindings[pair] = binding
            for row in publication.cohort.rows:
                written += self._write_bea_observation(
                    connection,
                    row=row,
                    binding=bindings[(row.table_name, row.series_code)],
                    publication=publication,
                    run_id=active_run_id,
                )
            if not artifacts:
                raise ConflictError("Stage 11 BEA changed publication lacks new immutable evidence")
            quality = _quality_results(
                prefix="stage11_bea_quality",
                semantic_identity=publication.semantic_identity,
                snapshot_id=publication.snapshot_id,
                evidence_dataset_id=BEA_NIPA_HISTORY_EVIDENCE_DATASET_ID,
                canonical_dataset_id=BEA_NIPA_HISTORY_DATASET_ID,
                artifact_id=artifacts[0].artifact_id,
                observed={"rows": len(publication.cohort.rows), "captures": len(artifacts), "complete": True},
                canonical_rule="local_capture_correction_chain",
            )
            return WriteResult(
                written_count=written,
                artifacts=tuple(artifacts),
                snapshot=SnapshotWrite(
                    snapshot_id=publication.snapshot_id,
                    dataset_id=BEA_NIPA_HISTORY_DATASET_ID,
                    semantic_identity=publication.semantic_identity,
                    scope=dict(publication.request_scope),
                    completeness="complete",
                    row_count=len(publication.cohort.rows),
                    captured_at=publication.captured_at,
                    captured_precision="datetime",
                    validation_state="validated",
                    artifact_ids=tuple(item.artifact_id for item in artifacts),
                ),
                quality_results=quality,
            )

        return self._coordinator.execute(
            role=StoreRole.MACRO,
            dataset_id=BEA_NIPA_HISTORY_DATASET_ID,
            output_dataset_ids=_BEA_OUTPUT_DATASETS,
            semantic_identity=publication.semantic_identity,
            run_id=publication.run_id,
            command=BEA_NIPA_HISTORY_COLLECTOR_ID,
            scope=dict(publication.request_scope),
            started_at=publication.captured_at,
            completed_at=publication.captured_at,
            fetched_count=len(publication.cohort.rows),
            writer=writer,
            held_locks=held_locks,
        )

    @staticmethod
    def _write_bea_observation(
        connection: sqlite3.Connection,
        *,
        row: BeaNipaObservation,
        binding: _CaptureBinding,
        publication: _BeaPublication,
        run_id: str,
    ) -> int:
        canonical_series_id = row.canonical_series_id
        current = connection.execute(
            """
            SELECT version.version_id, version.correction_sequence, version.value_text,
                   version.unit, version.unit_multiplier
            FROM stage11_bea_nipa_observations AS current
            JOIN stage11_bea_nipa_observation_versions AS version
              ON version.version_id=current.current_version_id
            WHERE current.canonical_series_id=? AND current.period=?
            """,
            (canonical_series_id, row.period),
        ).fetchone()
        value_text = _decimal_text(row.value)
        multiplier = int(row.unit_mult)
        if current is not None and (
            current["value_text"], current["unit"], current["unit_multiplier"]
        ) == (value_text, row.cl_unit, multiplier):
            return 0
        sequence = 1 if current is None else int(current["correction_sequence"]) + 1
        supersedes = None if current is None else str(current["version_id"])
        version_id = stable_id(
            "stage11_bea_nipa_version",
            canonical_series_id,
            row.period,
            str(sequence),
            publication.semantic_identity,
        )
        connection.execute(
            """
            INSERT INTO stage11_bea_nipa_observation_versions (
                version_id, canonical_series_id, table_name, series_code, period,
                value_text, unit, unit_multiplier, available_at, available_precision,
                captured_at, captured_precision, correction_sequence,
                supersedes_version_id, capture_id, artifact_id, snapshot_id, run_id,
                source_row
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'datetime', ?, 'datetime', ?, ?,
                      ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                canonical_series_id,
                row.table_name,
                row.series_code,
                row.period,
                value_text,
                row.cl_unit,
                multiplier,
                publication.captured_at,
                publication.captured_at,
                sequence,
                supersedes,
                binding.capture_id,
                binding.artifact_id,
                binding.snapshot_id,
                run_id,
                row.source_row,
            ),
        )
        if current is None:
            connection.execute(
                """
                INSERT INTO stage11_bea_nipa_observations (
                    canonical_series_id, period, current_version_id
                ) VALUES (?, ?, ?)
                """,
                (canonical_series_id, row.period, version_id),
            )
        else:
            connection.execute(
                """
                UPDATE stage11_bea_nipa_observations SET current_version_id=?
                WHERE canonical_series_id=? AND period=?
                """,
                (version_id, canonical_series_id, row.period),
            )
        return 1

    def _publish_retail(
        self, publication: _RetailPublication, *, held_locks: HeldWriteLocks | None
    ) -> IngestionReceipt:
        def writer(connection: sqlite3.Connection, active_run_id: str) -> WriteResult:
            if active_run_id != publication.run_id:
                raise ValidationError("Ingestion coordinator supplied an unexpected run identity")
            bindings: dict[int, _CaptureBinding] = {}
            artifacts: list[ArtifactWrite] = []
            written = 0
            for page in publication.cohort.pages:
                binding = self._existing_retail_capture(connection, page)
                if binding is None:
                    scope = page.request.request_scope()
                    scope_sha256 = _sha256_json(scope)
                    capture_semantic = _sha256_json(
                        {
                            "publication_semantic_identity": publication.semantic_identity,
                            "request_scope": scope,
                            "response_sha256": page.raw_bytes_sha256,
                            "normalized_page_sha256": page.semantic_sha256,
                        }
                    )
                    capture_id = stable_id("stage11_eia_retail_capture", capture_semantic)
                    artifact_id = stable_id(
                        "stage11_eia_retail_artifact",
                        EIA_ELECTRICITY_RETAIL_HISTORY_EVIDENCE_DATASET_ID,
                        page.raw_bytes_sha256,
                        scope_sha256,
                    )
                    capture_snapshot_id = stable_id(
                        "stage11_eia_retail_capture_snapshot", capture_semantic
                    )
                    connection.execute(
                        """
                        INSERT INTO stage11_eia_retail_captures (
                            capture_id, dataset_id, provider, endpoint_path,
                            request_scope_json, request_scope_sha256, response_sha256,
                            response_bytes, semantic_identity, completeness,
                            availability_basis, state_id, sector_id, page_offset,
                            page_length, page_total, artifact_id, snapshot_id,
                            captured_at, captured_precision, row_count,
                            normalization_version, run_id
                        ) VALUES (?, ?, 'eia', ?, ?, ?, ?, ?, ?, 'complete',
                                  'local_capture', 'US', 'ALL', ?, ?, ?, ?, ?, ?,
                                  'datetime', ?, ?, ?)
                        """,
                        (
                            capture_id,
                            EIA_ELECTRICITY_RETAIL_HISTORY_EVIDENCE_DATASET_ID,
                            page.request.endpoint_path,
                            dumps_strict(scope),
                            scope_sha256,
                            page.raw_bytes_sha256,
                            page.raw_bytes,
                            capture_semantic,
                            page.request.offset,
                            EIA_RETAIL_PAGE_LENGTH,
                            page.total,
                            artifact_id,
                            capture_snapshot_id,
                            publication.captured_at,
                            page.raw_row_count,
                            EIA_RETAIL_NORMALIZATION_VERSION,
                            active_run_id,
                        ),
                    )
                    binding = _CaptureBinding(capture_id, artifact_id, capture_snapshot_id, True)
                    artifacts.append(
                        _capture_artifact(
                            artifact_id=artifact_id,
                            dataset_id=EIA_ELECTRICITY_RETAIL_HISTORY_EVIDENCE_DATASET_ID,
                            response_sha256=page.raw_bytes_sha256,
                            response_bytes=page.raw_bytes,
                            request_scope=scope,
                            captured_at=publication.captured_at,
                            normalization_version=EIA_RETAIL_NORMALIZATION_VERSION,
                            source_reference="eia/electricity_retail_history",
                        )
                    )
                    written += 1
                bindings[page.request.offset] = binding
            for page in publication.cohort.pages:
                binding = bindings[page.request.offset]
                for row in page.rows:
                    written += self._write_retail_observation(
                        connection,
                        row=row,
                        binding=binding,
                        publication=publication,
                        run_id=active_run_id,
                    )
            if not artifacts:
                raise ConflictError("Stage 11 retail changed publication lacks new immutable evidence")
            quality = _quality_results(
                prefix="stage11_eia_retail_quality",
                semantic_identity=publication.semantic_identity,
                snapshot_id=publication.snapshot_id,
                evidence_dataset_id=EIA_ELECTRICITY_RETAIL_HISTORY_EVIDENCE_DATASET_ID,
                canonical_dataset_id=EIA_ELECTRICITY_RETAIL_HISTORY_DATASET_ID,
                artifact_id=artifacts[0].artifact_id,
                observed={
                    "rows": len(publication.cohort.rows),
                    "pages": len(publication.cohort.pages),
                    "complete": True,
                    "tombstone_authoritative": False,
                },
                canonical_rule="complete_pagination_correction_chain",
            )
            return WriteResult(
                written_count=written,
                artifacts=tuple(artifacts),
                snapshot=SnapshotWrite(
                    snapshot_id=publication.snapshot_id,
                    dataset_id=EIA_ELECTRICITY_RETAIL_HISTORY_DATASET_ID,
                    semantic_identity=publication.semantic_identity,
                    scope=dict(publication.request_scope),
                    completeness="complete",
                    row_count=len(publication.cohort.rows),
                    captured_at=publication.captured_at,
                    captured_precision="datetime",
                    validation_state="validated",
                    artifact_ids=tuple(item.artifact_id for item in artifacts),
                ),
                quality_results=quality,
            )

        return self._coordinator.execute(
            role=StoreRole.MACRO,
            dataset_id=EIA_ELECTRICITY_RETAIL_HISTORY_DATASET_ID,
            output_dataset_ids=_EIA_RETAIL_OUTPUT_DATASETS,
            semantic_identity=publication.semantic_identity,
            run_id=publication.run_id,
            command=EIA_ELECTRICITY_RETAIL_HISTORY_COLLECTOR_ID,
            scope=dict(publication.request_scope),
            started_at=publication.captured_at,
            completed_at=publication.captured_at,
            fetched_count=len(publication.cohort.rows),
            writer=writer,
            held_locks=held_locks,
        )

    @staticmethod
    def _write_retail_observation(
        connection: sqlite3.Connection,
        *,
        row: EiaRetailObservation,
        binding: _CaptureBinding,
        publication: _RetailPublication,
        run_id: str,
    ) -> int:
        canonical_series_id = row.canonical_series_id
        current = connection.execute(
            """
            SELECT version.version_id, version.correction_sequence, version.value_text,
                   version.unit
            FROM stage11_eia_retail_observations AS current
            JOIN stage11_eia_retail_observation_versions AS version
              ON version.version_id=current.current_version_id
            WHERE current.canonical_series_id=? AND current.period=?
              AND current.state_id='US' AND current.sector_id='ALL'
            """,
            (canonical_series_id, row.period),
        ).fetchone()
        value_text = _decimal_text(row.value)
        if current is not None and (current["value_text"], current["unit"]) == (
            value_text,
            row.unit,
        ):
            return 0
        sequence = 1 if current is None else int(current["correction_sequence"]) + 1
        supersedes = None if current is None else str(current["version_id"])
        version_id = stable_id(
            "stage11_eia_retail_version",
            canonical_series_id,
            row.period,
            str(sequence),
            publication.semantic_identity,
        )
        connection.execute(
            """
            INSERT INTO stage11_eia_retail_observation_versions (
                version_id, canonical_series_id, metric, period, state_id, sector_id,
                value_text, unit, available_at, available_precision, captured_at,
                captured_precision, correction_sequence, supersedes_version_id,
                capture_id, artifact_id, snapshot_id, run_id, source_row
            ) VALUES (?, ?, ?, ?, 'US', 'ALL', ?, ?, ?, 'datetime', ?, 'datetime',
                      ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                canonical_series_id,
                row.metric,
                row.period,
                value_text,
                row.unit,
                publication.captured_at,
                publication.captured_at,
                sequence,
                supersedes,
                binding.capture_id,
                binding.artifact_id,
                binding.snapshot_id,
                run_id,
                row.source_row,
            ),
        )
        if current is None:
            connection.execute(
                """
                INSERT INTO stage11_eia_retail_observations (
                    canonical_series_id, period, state_id, sector_id, current_version_id
                ) VALUES (?, ?, 'US', 'ALL', ?)
                """,
                (canonical_series_id, row.period, version_id),
            )
        else:
            connection.execute(
                """
                UPDATE stage11_eia_retail_observations SET current_version_id=?
                WHERE canonical_series_id=? AND period=? AND state_id='US' AND sector_id='ALL'
                """,
                (version_id, canonical_series_id, row.period),
            )
        return 1

    def _publish_weekly(
        self, publication: _WeeklyPublication, *, held_locks: HeldWriteLocks | None
    ) -> IngestionReceipt:
        def writer(connection: sqlite3.Connection, active_run_id: str) -> WriteResult:
            if active_run_id != publication.run_id:
                raise ValidationError("Ingestion coordinator supplied an unexpected run identity")
            capture = publication.capture
            binding = self._existing_weekly_capture(connection, capture)
            artifacts: list[ArtifactWrite] = []
            written = 0
            if binding is None:
                scope = capture.request_scope
                scope_sha256 = _sha256_json(scope)
                capture_semantic = _sha256_json(
                    {
                        "publication_semantic_identity": publication.semantic_identity,
                        "request_scope": scope,
                        "response_sha256": capture.raw_bytes_sha256,
                        "normalized_history_sha256": capture.semantic_sha256,
                    }
                )
                capture_id = stable_id("stage11_eia_weekly_capture", capture_semantic)
                artifact_id = stable_id(
                    "stage11_eia_weekly_artifact",
                    EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_EVIDENCE_DATASET_ID,
                    capture.raw_bytes_sha256,
                    scope_sha256,
                )
                capture_snapshot_id = stable_id(
                    "stage11_eia_weekly_capture_snapshot", capture_semantic
                )
                connection.execute(
                    """
                    INSERT INTO stage11_eia_weekly_captures (
                        capture_id, dataset_id, provider, endpoint_path, provider_series_id,
                        request_scope_json, request_scope_sha256, response_sha256,
                        response_bytes, semantic_identity, completeness, availability_basis,
                        artifact_id, snapshot_id, captured_at, captured_precision, row_count,
                        normalization_version, run_id
                    ) VALUES (?, ?, 'eia', ?, 'PET.WCESTUS1.W', ?, ?, ?, ?, ?,
                              'complete', 'local_capture', ?, ?, ?, 'datetime', ?, ?, ?)
                    """,
                    (
                        capture_id,
                        EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_EVIDENCE_DATASET_ID,
                        capture.request.endpoint_path,
                        dumps_strict(scope),
                        scope_sha256,
                        capture.raw_bytes_sha256,
                        capture.raw_bytes,
                        capture_semantic,
                        artifact_id,
                        capture_snapshot_id,
                        publication.captured_at,
                        len(capture.rows),
                        EIA_WEEKLY_NORMALIZATION_VERSION,
                        active_run_id,
                    ),
                )
                binding = _CaptureBinding(capture_id, artifact_id, capture_snapshot_id, True)
                artifacts.append(
                    _capture_artifact(
                        artifact_id=artifact_id,
                        dataset_id=EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_EVIDENCE_DATASET_ID,
                        response_sha256=capture.raw_bytes_sha256,
                        response_bytes=capture.raw_bytes,
                        request_scope=scope,
                        captured_at=publication.captured_at,
                        normalization_version=EIA_WEEKLY_NORMALIZATION_VERSION,
                        source_reference="eia/petroleum_weekly_stock_history",
                    )
                )
                written += 1
            for row in capture.rows:
                written += self._write_weekly_observation(
                    connection,
                    row=row,
                    binding=binding,
                    publication=publication,
                    run_id=active_run_id,
                )
            if not artifacts:
                raise ConflictError("Stage 11 weekly changed publication lacks new immutable evidence")
            quality = _quality_results(
                prefix="stage11_eia_weekly_quality",
                semantic_identity=publication.semantic_identity,
                snapshot_id=publication.snapshot_id,
                evidence_dataset_id=EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_EVIDENCE_DATASET_ID,
                canonical_dataset_id=EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_DATASET_ID,
                artifact_id=artifacts[0].artifact_id,
                observed={"rows": len(capture.rows), "complete": True, "series": EIA_WEEKLY_SERIES_ID},
                canonical_rule="content_identity_correction_chain",
            )
            return WriteResult(
                written_count=written,
                artifacts=tuple(artifacts),
                snapshot=SnapshotWrite(
                    snapshot_id=publication.snapshot_id,
                    dataset_id=EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_DATASET_ID,
                    semantic_identity=publication.semantic_identity,
                    scope=dict(publication.request_scope),
                    completeness="complete",
                    row_count=len(capture.rows),
                    captured_at=publication.captured_at,
                    captured_precision="datetime",
                    validation_state="validated",
                    artifact_ids=tuple(item.artifact_id for item in artifacts),
                ),
                quality_results=quality,
            )

        return self._coordinator.execute(
            role=StoreRole.MACRO,
            dataset_id=EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_DATASET_ID,
            output_dataset_ids=_EIA_WEEKLY_OUTPUT_DATASETS,
            semantic_identity=publication.semantic_identity,
            run_id=publication.run_id,
            command=EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_COLLECTOR_ID,
            scope=dict(publication.request_scope),
            started_at=publication.captured_at,
            completed_at=publication.captured_at,
            fetched_count=len(publication.capture.rows),
            writer=writer,
            held_locks=held_locks,
        )

    @staticmethod
    def _write_weekly_observation(
        connection: sqlite3.Connection,
        *,
        row: EiaWeeklyObservation,
        binding: _CaptureBinding,
        publication: _WeeklyPublication,
        run_id: str,
    ) -> int:
        current = connection.execute(
            """
            SELECT version.version_id, version.correction_sequence, version.value_text,
                   version.unit, version.content_sha256
            FROM stage11_eia_weekly_observations AS current
            JOIN stage11_eia_weekly_observation_versions AS version
              ON version.version_id=current.current_version_id
            WHERE current.canonical_series_id=? AND current.period=?
            """,
            (EIA_WEEKLY_CANONICAL_SERIES_ID, row.period),
        ).fetchone()
        value_text = _decimal_text(row.value)
        if current is not None and (
            current["value_text"], current["unit"], current["content_sha256"]
        ) == (value_text, row.unit, row.content_sha256):
            return 0
        sequence = 1 if current is None else int(current["correction_sequence"]) + 1
        supersedes = None if current is None else str(current["version_id"])
        version_id = stable_id(
            "stage11_eia_weekly_version",
            EIA_WEEKLY_CANONICAL_SERIES_ID,
            row.period,
            str(sequence),
            publication.semantic_identity,
        )
        connection.execute(
            """
            INSERT INTO stage11_eia_weekly_observation_versions (
                version_id, canonical_series_id, provider_series_id, period, value_text,
                unit, content_sha256, available_at, available_precision, captured_at,
                captured_precision, correction_sequence, supersedes_version_id,
                capture_id, artifact_id, snapshot_id, run_id, source_row
            ) VALUES (?, ?, 'PET.WCESTUS1.W', ?, ?, ?, ?, ?, 'datetime', ?, 'datetime',
                      ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                EIA_WEEKLY_CANONICAL_SERIES_ID,
                row.period,
                value_text,
                row.unit,
                row.content_sha256,
                publication.captured_at,
                publication.captured_at,
                sequence,
                supersedes,
                binding.capture_id,
                binding.artifact_id,
                binding.snapshot_id,
                run_id,
                row.source_row,
            ),
        )
        if current is None:
            connection.execute(
                """
                INSERT INTO stage11_eia_weekly_observations (
                    canonical_series_id, period, current_version_id
                ) VALUES (?, ?, ?)
                """,
                (EIA_WEEKLY_CANONICAL_SERIES_ID, row.period, version_id),
            )
        else:
            connection.execute(
                """
                UPDATE stage11_eia_weekly_observations SET current_version_id=?
                WHERE canonical_series_id=? AND period=?
                """,
                (version_id, EIA_WEEKLY_CANONICAL_SERIES_ID, row.period),
            )
        return 1


__all__ = (
    "BEA_NIPA_HISTORY_COLLECTOR_ID",
    "BEA_NIPA_HISTORY_DATASET_ID",
    "BEA_NIPA_HISTORY_EVIDENCE_DATASET_ID",
    "BEA_NIPA_RELATIONS",
    "EIA_ELECTRICITY_RETAIL_HISTORY_COLLECTOR_ID",
    "EIA_ELECTRICITY_RETAIL_HISTORY_DATASET_ID",
    "EIA_ELECTRICITY_RETAIL_HISTORY_EVIDENCE_DATASET_ID",
    "EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_COLLECTOR_ID",
    "EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_DATASET_ID",
    "EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_EVIDENCE_DATASET_ID",
    "EIA_RETAIL_RELATIONS",
    "EIA_WEEKLY_RELATIONS",
    "PreparedStage11MacroPublication",
    "STAGE11_MIGRATION_ID",
    "STAGE11_MIGRATION_RESOURCE",
    "STAGE11_MIGRATION_SHA256",
    "Stage11MacroImporter",
)
