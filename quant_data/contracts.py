"""Immutable typed contracts used by repositories and public adapters."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Mapping

from .errors import ValidationError
from .json_codec import dumps_strict


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
            "metadata": dict(self.metadata),
            "observations": [observation.to_primitive() for observation in self.observations],
            "warnings": list(self.warnings),
            "audit": dict(self.audit),
            "provenance": dict(self.provenance),
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
