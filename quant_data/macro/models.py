"""Immutable macro-domain candidates used by the Stage 1 fixture lane.

These are intentionally internal domain values.  Public callers use the
repository contracts exported from :mod:`quant_data.macro` rather than these
write-side candidates.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping

from ..temporal import TemporalValue


@dataclass(frozen=True, slots=True)
class MacroSeriesCandidate:
    """Stable metadata for one RTDSM-style macro series."""

    series_id: str
    provider: str
    provider_series_code: str
    title: str
    frequency: str
    unit: str
    value_representation: str
    scale: str
    dimensions_json: str
    dimensions_digest: str


@dataclass(frozen=True, slots=True)
class MacroReleaseCandidate:
    """A source-defined release/vintage, independent of local capture time."""

    source_vintage_identity: str
    vintage_at: TemporalValue
    source_release_order: str
    available_at: TemporalValue
    is_first_release: bool
    first_release_evidence: str | None


@dataclass(frozen=True, slots=True)
class MacroObservationCandidate:
    """One normalized native-period observation from a complete snapshot."""

    period_start: str
    period_end: str
    dimensions_json: str
    dimensions_digest: str
    value: Decimal | None
    missing_reason: str | None
    source_row: int


@dataclass(frozen=True, slots=True)
class MacroFixtureCandidate:
    """A fully verified, normalized, and batch-validated fixture payload."""

    fixture_id: str
    ingestion_family_id: str
    evidence_dataset_id: str
    canonical_dataset_id: str
    provider: str
    artifact_sha256: str
    artifact_byte_count: int
    artifact_resource: str
    captured_at: TemporalValue
    normalization_version: str
    request_scope: Mapping[str, object]
    scope_digest: str
    semantic_identity: str
    series: MacroSeriesCandidate
    release: MacroReleaseCandidate
    observations: tuple[MacroObservationCandidate, ...]
