"""Immutable typed contracts used by repositories and public adapters."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Mapping

from .errors import ValidationError
from .json_codec import dumps_strict


def _freeze_contract_value(value: Any, *, field_name: str) -> Any:
    """Return a detached, recursively immutable JSON-contract value."""

    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for name, child in value.items():
            if not isinstance(name, str):
                raise ValidationError(f"{field_name} keys must be strings")
            normalized[name] = _freeze_contract_value(
                child, field_name=f"{field_name}.{name}"
            )
        return MappingProxyType(normalized)
    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_contract_value(child, field_name=field_name) for child in value
        )
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValidationError(f"{field_name} values must be finite")
        return value
    if isinstance(value, float):
        raise ValidationError(f"{field_name} values must not use binary floats")
    if isinstance(value, (str, int, bool, type(None))):
        return value
    raise ValidationError(f"{field_name} contains an unsupported typed value")


def _public_contract_value(value: Any) -> Any:
    """Return a detached JSON-boundary value from an immutable contract value."""

    if isinstance(value, Mapping):
        return {name: _public_contract_value(child) for name, child in value.items()}
    if isinstance(value, tuple):
        return [_public_contract_value(child) for child in value]
    return value


@dataclass(frozen=True, slots=True)
class Observation:
    period_start: str
    period_end: str
    value: Decimal | None
    missing_reason: str | None
    unit: str
    value_representation: str
    scale: str
    vintage_at: str
    available_at: str | None
    available_precision: str
    captured_at: str
    captured_precision: str
    version_id: str
    evidence_id: str
    snapshot_id: str
    run_id: str
    dimensions: Mapping[str, str] = field(default_factory=dict)
    quality_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.dimensions, Mapping) or not all(
            isinstance(name, str)
            and isinstance(value, str)
            and name
            and value
            for name, value in self.dimensions.items()
        ):
            raise ValidationError("Observation dimensions must be string pairs")
        object.__setattr__(
            self, "dimensions", MappingProxyType(dict(self.dimensions))
        )
        object.__setattr__(self, "quality_flags", tuple(self.quality_flags))
        has_value = self.value is not None
        has_missing = self.missing_reason is not None
        if has_value == has_missing:
            raise ValidationError(
                "Observation must contain exactly one of value or missing_reason"
            )
        if self.value is not None and not self.value.is_finite():
            raise ValidationError("Observation value must be finite")

    def to_primitive(self) -> dict[str, Any]:
        return {
            "period_start": self.period_start,
            "period_end": self.period_end,
            "value": self.value,
            "missing_reason": self.missing_reason,
            "unit": self.unit,
            "value_representation": self.value_representation,
            "scale": self.scale,
            "vintage_at": self.vintage_at,
            "available_at": self.available_at,
            "available_precision": self.available_precision,
            "captured_at": self.captured_at,
            "captured_precision": self.captured_precision,
            "version_id": self.version_id,
            "evidence_id": self.evidence_id,
            "snapshot_id": self.snapshot_id,
            "run_id": self.run_id,
            "dimensions": dict(self.dimensions),
            "quality_flags": list(self.quality_flags),
        }


@dataclass(frozen=True, slots=True)
class TimeSeries:
    series_id: str
    metadata: Mapping[str, Any]
    observations: tuple[Observation, ...]
    warnings: tuple[str, ...]
    audit: Mapping[str, Any]
    provenance: Mapping[str, Any]
    truncated: bool = False
    lineage_digest: str = ""

    def __post_init__(self) -> None:
        if not self.series_id:
            raise ValidationError("TimeSeries series_id cannot be empty")
        object.__setattr__(self, "observations", tuple(self.observations))
        object.__setattr__(self, "warnings", tuple(self.warnings))
        for field_name in ("metadata", "audit", "provenance"):
            value = getattr(self, field_name)
            if not isinstance(value, Mapping):
                raise ValidationError(f"TimeSeries {field_name} must be a mapping")
            object.__setattr__(
                self,
                field_name,
                _freeze_contract_value(
                    value, field_name=f"TimeSeries {field_name}"
                ),
            )
        if not all(isinstance(observation, Observation) for observation in self.observations):
            raise ValidationError("TimeSeries observations must use the typed contract")
        if not all(isinstance(warning, str) for warning in self.warnings):
            raise ValidationError("TimeSeries warnings must be strings")
        digest = self.expected_lineage_digest()
        if self.lineage_digest and self.lineage_digest != digest:
            raise ValidationError("TimeSeries lineage digest does not match its contents")
        if not self.lineage_digest:
            object.__setattr__(self, "lineage_digest", digest)

    def lineage_material(self) -> dict[str, Any]:
        """Return every semantic public field except the digest itself."""

        return {
            "contract": "quant_data.timeseries",
            "contract_version": "1.0.0",
            "series_id": self.series_id,
            "metadata": _public_contract_value(self.metadata),
            "observations": [observation.to_primitive() for observation in self.observations],
            "warnings": list(self.warnings),
            "audit": _public_contract_value(self.audit),
            "provenance": _public_contract_value(self.provenance),
            "truncated": self.truncated,
        }

    def expected_lineage_digest(self) -> str:
        return hashlib.sha256(
            dumps_strict(self.lineage_material()).encode("utf-8")
        ).hexdigest()

    def validate_lineage(self) -> None:
        """Revalidate callers that may retain mutable nested mappings."""

        if self.lineage_digest != self.expected_lineage_digest():
            raise ValidationError("TimeSeries lineage digest does not match its contents")

    def to_primitive(self) -> dict[str, Any]:
        return {**self.lineage_material(), "lineage_digest": self.lineage_digest}


@dataclass(frozen=True, slots=True)
class MacroReleaseRefV2:
    """Source-native release identity for one canonical macro observation."""

    release_id: str
    source_vintage_identity: str
    source_release_order: str
    vintage_at: str | None
    vintage_precision: str | None
    availability_basis: str
    is_first_release: bool | None
    first_release_evidence: str | None
    release_stage: str | None

    def __post_init__(self) -> None:
        if (
            not self.release_id
            or not self.source_vintage_identity
            or not self.source_release_order
            or not self.availability_basis
            or self.vintage_precision not in {None, "date", "datetime"}
            or (self.vintage_at is None) != (self.vintage_precision is None)
            or (
                self.is_first_release is True
                and not self.first_release_evidence
            )
        ):
            raise ValidationError("Macro release reference is invalid")

    def to_primitive(self) -> dict[str, Any]:
        return {
            "release_id": self.release_id,
            "source_vintage_identity": self.source_vintage_identity,
            "source_release_order": self.source_release_order,
            "vintage_at": self.vintage_at,
            "vintage_precision": self.vintage_precision,
            "availability_basis": self.availability_basis,
            "is_first_release": self.is_first_release,
            "first_release_evidence": self.first_release_evidence,
            "release_stage": self.release_stage,
        }


@dataclass(frozen=True, slots=True)
class MacroAvailabilityV2:
    at: str
    precision: str

    def __post_init__(self) -> None:
        if not self.at or self.precision not in {"date", "datetime"}:
            raise ValidationError("Macro availability is invalid")

    def to_primitive(self) -> dict[str, str]:
        return {"at": self.at, "precision": self.precision}


@dataclass(frozen=True, slots=True)
class MacroCaptureRefV2:
    captured_at: str
    captured_precision: str

    def __post_init__(self) -> None:
        if not self.captured_at or self.captured_precision != "datetime":
            raise ValidationError("Macro capture reference is invalid")

    def to_primitive(self) -> dict[str, str]:
        return {
            "captured_at": self.captured_at,
            "captured_precision": self.captured_precision,
        }


@dataclass(frozen=True, slots=True)
class MacroEvidenceRefV2:
    """Discriminated source reference without invented snapshot/run IDs."""

    kind: str
    id: str
    snapshot_id: str | None
    run_id: str | None

    def __post_init__(self) -> None:
        if not self.id or self.kind not in {"artifact", "capture"}:
            raise ValidationError("Macro evidence reference is invalid")
        if self.kind == "artifact" and (not self.snapshot_id or not self.run_id):
            raise ValidationError("Macro artifact evidence requires snapshot and run IDs")
        if self.kind == "capture" and (
            self.snapshot_id is not None or self.run_id is not None
        ):
            raise ValidationError("Macro capture evidence cannot invent snapshot or run IDs")

    def to_primitive(self) -> dict[str, str | None]:
        return {
            "kind": self.kind,
            "id": self.id,
            "snapshot_id": self.snapshot_id,
            "run_id": self.run_id,
        }


@dataclass(frozen=True, slots=True)
class MacroDimensionV2:
    """One deterministic name/value dimension on a macro observation."""

    name: str
    value: str

    def __post_init__(self) -> None:
        if not self.name or not self.value:
            raise ValidationError("Macro dimension is invalid")

    def to_primitive(self) -> dict[str, str]:
        return {"name": self.name, "value": self.value}


@dataclass(frozen=True, slots=True)
class MacroObservationV2:
    """One source-native canonical macro value selected by the v2 gateway."""

    period_start: str
    period_end: str
    source_period: str
    value: Decimal | None
    missing_reason: str | None
    unit: str
    value_representation: str
    scale: str | None
    dimensions: tuple[MacroDimensionV2, ...]
    version_id: str
    correction_sequence: int
    source_row: int
    release: MacroReleaseRefV2
    availability: MacroAvailabilityV2
    capture: MacroCaptureRefV2
    evidence: MacroEvidenceRefV2

    def __post_init__(self) -> None:
        if (
            not self.period_start
            or not self.period_end
            or self.period_end < self.period_start
            or not self.source_period
            or not self.unit
            or not self.value_representation
            or not self.version_id
            or isinstance(self.correction_sequence, bool)
            or self.correction_sequence < 1
            or isinstance(self.source_row, bool)
            or self.source_row < 1
        ):
            raise ValidationError("Macro observation is invalid")
        has_value = self.value is not None
        has_missing = self.missing_reason is not None
        if has_value == has_missing:
            raise ValidationError(
                "Macro observation must have exactly one value or missing reason"
            )
        if self.value is not None and not self.value.is_finite():
            raise ValidationError("Macro observation value must be finite")
        dimensions = tuple(self.dimensions)
        if (
            not all(isinstance(item, MacroDimensionV2) for item in dimensions)
            or tuple(item.name for item in dimensions)
            != tuple(sorted(item.name for item in dimensions))
            or len({item.name for item in dimensions}) != len(dimensions)
        ):
            raise ValidationError("Macro observation dimensions are invalid")
        object.__setattr__(self, "dimensions", dimensions)
        if not isinstance(self.release, MacroReleaseRefV2):
            raise ValidationError("Macro observation release is invalid")
        if not isinstance(self.availability, MacroAvailabilityV2):
            raise ValidationError("Macro observation availability is invalid")
        if not isinstance(self.capture, MacroCaptureRefV2):
            raise ValidationError("Macro observation capture is invalid")
        if not isinstance(self.evidence, MacroEvidenceRefV2):
            raise ValidationError("Macro observation evidence is invalid")

    def to_primitive(self) -> dict[str, Any]:
        return {
            "period_start": self.period_start,
            "period_end": self.period_end,
            "source_period": self.source_period,
            "value": self.value,
            "missing_reason": self.missing_reason,
            "unit": self.unit,
            "value_representation": self.value_representation,
            "scale": self.scale,
            "dimensions": [item.to_primitive() for item in self.dimensions],
            "version_id": self.version_id,
            "correction_sequence": self.correction_sequence,
            "source_row": self.source_row,
            "release": self.release.to_primitive(),
            "availability": self.availability.to_primitive(),
            "capture": self.capture.to_primitive(),
            "evidence": self.evidence.to_primitive(),
        }


@dataclass(frozen=True, slots=True)
class MacroTimeSeriesV2:
    """Strict v2 macro series preserving nullable source-native provenance."""

    series_id: str
    metadata: Mapping[str, Any]
    observations: tuple[MacroObservationV2, ...]
    warnings: tuple[str, ...]
    audit: Mapping[str, Any]
    provenance: Mapping[str, Any]
    truncated: bool = False
    lineage_digest: str = ""

    def __post_init__(self) -> None:
        if not self.series_id:
            raise ValidationError("MacroTimeSeriesV2 series_id cannot be empty")
        object.__setattr__(self, "observations", tuple(self.observations))
        object.__setattr__(self, "warnings", tuple(self.warnings))
        if not all(
            isinstance(item, MacroObservationV2) for item in self.observations
        ):
            raise ValidationError("MacroTimeSeriesV2 observations are invalid")
        if not all(isinstance(item, str) and item for item in self.warnings):
            raise ValidationError("MacroTimeSeriesV2 warnings are invalid")
        for field_name in ("metadata", "audit", "provenance"):
            value = getattr(self, field_name)
            if not isinstance(value, Mapping):
                raise ValidationError(f"MacroTimeSeriesV2 {field_name} must be a mapping")
            object.__setattr__(
                self,
                field_name,
                _freeze_contract_value(value, field_name=f"MacroTimeSeriesV2 {field_name}"),
            )
        expected = self.expected_lineage_digest()
        if self.lineage_digest and self.lineage_digest != expected:
            raise ValidationError("MacroTimeSeriesV2 lineage digest is invalid")
        if not self.lineage_digest:
            object.__setattr__(self, "lineage_digest", expected)

    def lineage_material(self) -> dict[str, Any]:
        return {
            "contract": "quant_data.macro_timeseries",
            "contract_version": "2.0.0",
            "series_id": self.series_id,
            "metadata": _public_contract_value(self.metadata),
            "observations": [item.to_primitive() for item in self.observations],
            "warnings": list(self.warnings),
            "audit": _public_contract_value(self.audit),
            "provenance": _public_contract_value(self.provenance),
            "truncated": self.truncated,
        }

    def expected_lineage_digest(self) -> str:
        return hashlib.sha256(
            dumps_strict(self.lineage_material()).encode("utf-8")
        ).hexdigest()

    def validate_lineage(self) -> None:
        if self.lineage_digest != self.expected_lineage_digest():
            raise ValidationError("MacroTimeSeriesV2 lineage digest is invalid")

    def to_primitive(self) -> dict[str, Any]:
        return {**self.lineage_material(), "lineage_digest": self.lineage_digest}


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    store_map: Any
    registry_revision: str
    request_id: str
    max_rows: int = 10_000


@dataclass(frozen=True, slots=True)
class IngestionReceipt:
    outcome: str
    store: str
    dataset_id: str
    semantic_identity: str
    run_id: str | None
    artifact_id: str | None
    snapshot_id: str | None
    written_count: int
    warnings: tuple[str, ...] = ()

    def to_primitive(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "store": self.store,
            "dataset_id": self.dataset_id,
            "semantic_identity": self.semantic_identity,
            "run_id": self.run_id,
            "artifact_id": self.artifact_id,
            "snapshot_id": self.snapshot_id,
            "written_count": self.written_count,
            "warnings": list(self.warnings),
        }

_POINT_IN_TIME_STATUSES = frozenset(
    {"safe", "unsafe", "not_applicable", "not_established"}
)


@dataclass(frozen=True, slots=True)
class TemporalQuery:
    """Shared immutable availability contract for analytical operations."""

    mode: str
    cutoff: str | None
    date_only_policy: str
    availability_basis: str
    point_in_time_status: str
    unsafe_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.mode not in {"latest", "as_of", "first_release"}:
            raise ValidationError("Temporal query mode is unsupported")
        if self.mode == "as_of" and not self.cutoff:
            raise ValidationError("Temporal as_of queries require a cutoff")
        if self.mode != "as_of" and self.cutoff is not None:
            raise ValidationError("Temporal cutoff is only valid for as_of mode")
        if self.date_only_policy not in {
            "completed_date",
            "calendar_date_inclusive",
        }:
            raise ValidationError("Temporal date-only policy is unsupported")
        if not self.availability_basis:
            raise ValidationError("Temporal availability basis is required")
        if self.point_in_time_status not in _POINT_IN_TIME_STATUSES:
            raise ValidationError("Point-in-time status is unsupported")
        if self.point_in_time_status == "unsafe" and not self.unsafe_reasons:
            raise ValidationError("Unsafe temporal results require explicit reasons")
        if any(not isinstance(item, str) or not item for item in self.unsafe_reasons):
            raise ValidationError("Temporal unsafe reasons must be nonempty strings")

    def to_primitive(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "cutoff": self.cutoff,
            "date_only_policy": self.date_only_policy,
            "availability_basis": self.availability_basis,
            "point_in_time_status": self.point_in_time_status,
            "unsafe_reasons": list(self.unsafe_reasons),
        }


@dataclass(frozen=True, slots=True)
class LineageRef:
    """Path-free reference to immutable evidence or a canonical version."""

    dataset_id: str
    store_role: str
    semantic_id: str
    evidence_id: str | None = None
    snapshot_id: str | None = None
    canonical_version_id: str | None = None

    def __post_init__(self) -> None:
        if (
            not self.dataset_id
            or self.store_role not in {"market", "macro", "company", "news"}
            or not self.semantic_id
        ):
            raise ValidationError("Lineage reference identity is invalid")

    def to_primitive(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "store_role": self.store_role,
            "semantic_id": self.semantic_id,
            "evidence_id": self.evidence_id,
            "snapshot_id": self.snapshot_id,
            "canonical_version_id": self.canonical_version_id,
        }


@dataclass(frozen=True, slots=True)
class WarningV1:
    code: str
    message: str

    def __post_init__(self) -> None:
        if not self.code or not self.message:
            raise ValidationError("Warnings require a code and safe message")

    def to_primitive(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True, slots=True)
class ExclusionV1:
    code: str
    reason: str
    subject_id: str | None = None

    def __post_init__(self) -> None:
        if not self.code or not self.reason:
            raise ValidationError("Exclusions require a code and reason")

    def to_primitive(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "subject_id": self.subject_id,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class TruncationV1:
    applied: bool
    limit: int
    returned_count: int
    total_known_count: int | None
    has_more: bool
    next_cursor: str | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.limit, bool)
            or not isinstance(self.limit, int)
            or self.limit < 1
            or isinstance(self.returned_count, bool)
            or not isinstance(self.returned_count, int)
            or not 0 <= self.returned_count <= self.limit
        ):
            raise ValidationError("Truncation counts are invalid")
        if self.total_known_count is not None and self.total_known_count < self.returned_count:
            raise ValidationError("Truncation total cannot be below returned count")
        if self.applied != self.has_more:
            raise ValidationError("Truncation applied and has_more must agree")
        if not self.has_more and self.next_cursor is not None:
            raise ValidationError("A terminal result cannot expose a next cursor")

    def to_primitive(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "limit": self.limit,
            "returned_count": self.returned_count,
            "total_known_count": self.total_known_count,
            "has_more": self.has_more,
            "next_cursor": self.next_cursor,
        }



@dataclass(frozen=True, slots=True)
class ResearchContractV1:
    analysis_id: str
    analysis_kind: str
    model_id: str
    model_version: str
    parameters: Mapping[str, Decimal | str | int | bool | None]
    temporal: TemporalQuery
    point_in_time_status: str
    vintage_policy: str
    availability_policy: str
    execution_policy: str
    unsafe_reasons: tuple[str, ...] = ()
    sample: Mapping[str, Decimal | str | int | bool | None] = field(default_factory=dict)
    exclusions: tuple[ExclusionV1, ...] = ()
    uncertainty: Mapping[str, Decimal | str | int | bool | None] = field(default_factory=dict)
    input_lineage: tuple[LineageRef, ...] = ()

    def __post_init__(self) -> None:
        if (
            len(self.analysis_id) != 64
            or any(character not in "0123456789abcdef" for character in self.analysis_id)
            or not self.analysis_kind
            or not self.model_id
            or not self.model_version
        ):
            raise ValidationError("Research contract identity is invalid")
        if self.point_in_time_status not in _POINT_IN_TIME_STATUSES:
            raise ValidationError("Research point-in-time status is invalid")
        if self.point_in_time_status == "unsafe" and not self.unsafe_reasons:
            raise ValidationError("Unsafe research requires explicit reasons")
        if not isinstance(self.temporal, TemporalQuery):
            raise ValidationError("Research temporal contract is invalid")
        if self.point_in_time_status != self.temporal.point_in_time_status:
            raise ValidationError("Research point-in-time status must match temporal policy")
        if any(
            not isinstance(value, str) or not value
            for value in (self.vintage_policy, self.availability_policy, self.execution_policy)
        ):
            raise ValidationError("Research policy identifiers are required")
        if any(not isinstance(value, str) or not value for value in self.unsafe_reasons):
            raise ValidationError("Research unsafe reasons must be nonempty strings")
        if tuple(self.unsafe_reasons) != tuple(self.temporal.unsafe_reasons):
            raise ValidationError("Research unsafe reasons must match temporal policy")
        for field_name, values in (
            ("parameters", self.parameters),
            ("sample", self.sample),
            ("uncertainty", self.uncertainty),
        ):
            if not isinstance(values, Mapping):
                raise ValidationError(f"Research {field_name} must be a scalar mapping")
            normalized: dict[str, Decimal | str | int | bool | None] = {}
            for name, value in values.items():
                if not isinstance(name, str) or not name:
                    raise ValidationError(f"Research {field_name} names are invalid")
                if isinstance(value, float) or not isinstance(
                    value, (Decimal, str, int, bool, type(None))
                ):
                    raise ValidationError(f"Research {field_name} values must be JSON scalars")
                if isinstance(value, Decimal) and not value.is_finite():
                    raise ValidationError(f"Research {field_name} decimals must be finite")
                normalized[name] = value
            object.__setattr__(
                self,
                field_name,
                MappingProxyType(dict(sorted(normalized.items()))),
            )
        if not all(isinstance(value, ExclusionV1) for value in self.exclusions):
            raise ValidationError("Research exclusions must use typed contracts")
        if not all(isinstance(value, LineageRef) for value in self.input_lineage):
            raise ValidationError("Research input lineage must use typed contracts")

    def to_primitive(self) -> dict[str, Any]:
        return {
            "contract": "quant_data.research_contract",
            "contract_version": "1.0.0",
            "analysis_id": self.analysis_id,
            "analysis_kind": self.analysis_kind,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "parameters": [{"name": name, "value": value} for name, value in self.parameters.items()],
            "temporal": self.temporal.to_primitive(),
            "point_in_time_status": self.point_in_time_status,
            "unsafe_reasons": list(self.unsafe_reasons),
            "vintage_policy": self.vintage_policy,
            "availability_policy": self.availability_policy,
            "execution_policy": self.execution_policy,
            "sample": [{"name": name, "value": value} for name, value in self.sample.items()],
            "exclusions": [value.to_primitive() for value in self.exclusions],
            "uncertainty": [{"name": name, "value": value} for name, value in self.uncertainty.items()],
            "input_lineage": [value.to_primitive() for value in self.input_lineage],
        }
