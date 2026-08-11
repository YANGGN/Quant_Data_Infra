"""Typed, path-free contracts for the offline Atlas snapshot exporter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class AtlasProjection:
    """One deterministic public projection selected from the copy cohort."""

    projection_id: str
    operation_id: str
    dataset_id: str
    store: str
    schema_id: str
    fields: tuple[str, ...]
    rows: tuple[Mapping[str, object], ...]
    row_sha256: str
    warnings: tuple[str, ...] = ()

    def schema_primitive(self) -> dict[str, object]:
        return {
            "id": self.schema_id,
            "projection_id": self.projection_id,
            "operation_id": self.operation_id,
            "dataset_id": self.dataset_id,
            "store": self.store,
            "fields": list(self.fields),
        }


@dataclass(frozen=True, slots=True)
class AtlasPublication:
    """A path-free result for one immutable Atlas publication attempt."""

    export_id: str
    revision_id: str
    cutoff: str
    manifest_sha256: str
    receipt_sha256: str
    reused: bool
    current_revision_id: str

    def to_primitive(self) -> dict[str, object]:
        return {
            "export_id": self.export_id,
            "revision_id": self.revision_id,
            "cutoff": self.cutoff,
            "manifest_sha256": self.manifest_sha256,
            "receipt_sha256": self.receipt_sha256,
            "reused": self.reused,
            "current_revision_id": self.current_revision_id,
        }
