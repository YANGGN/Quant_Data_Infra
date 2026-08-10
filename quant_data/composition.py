"""Bounded, read-only application-memory composition across registered stores."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping

from .errors import ConflictError, ResourceLimitError, ValidationError
from .json_codec import dumps_strict
from .registry import Registry
from .stores import StoreMap, dataset_read_connection
from .temporal import DateOnlyPolicy, TemporalValue, availability_at_or_before


_MAX_COMPONENT_LIMIT = 10_000


@dataclass(frozen=True, slots=True)
class CompositionContext:
    cutoff: str
    date_only_policy: DateOnlyPolicy | str
    component_limit: int
    joined_limit: int

    def __post_init__(self) -> None:
        cutoff = TemporalValue.parse(self.cutoff, pointer="/cutoff")
        try:
            policy = DateOnlyPolicy(self.date_only_policy)
        except (TypeError, ValueError) as exc:
            raise ValidationError("Unsupported composition date-only policy") from exc
        for name, value in (
            ("component_limit", self.component_limit),
            ("joined_limit", self.joined_limit),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 1 <= value <= _MAX_COMPONENT_LIMIT
            ):
                raise ResourceLimitError(f"{name} is outside the supported range")
        object.__setattr__(self, "cutoff", cutoff.raw)
        object.__setattr__(self, "date_only_policy", policy)


@dataclass(frozen=True, slots=True)
class ComponentRow:
    join_identity: str
    available_at: str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.join_identity, str) or not self.join_identity:
            raise ValidationError("Composition join identity must be nonempty")
        TemporalValue.parse(self.available_at, pointer="/available_at")
        if not isinstance(self.payload, Mapping):
            raise ValidationError("Composition payload must be an object")


@dataclass(frozen=True, slots=True)
class ComponentBatch:
    dataset_id: str
    store_role: str
    rows: tuple[ComponentRow, ...]
    store_receipt: Mapping[str, Any]


class BoundedCrossStoreComposer:
    """Compose trusted registered component batches without cross-DB SQL."""

    def __init__(self, registry: Registry) -> None:
        self._registry = registry

    def compose(
        self,
        *,
        context: CompositionContext,
        components: tuple[ComponentBatch, ...],
    ) -> dict[str, Any]:
        if len(components) < 2:
            raise ValidationError("Cross-store composition requires at least two components")
        ordered_components = tuple(sorted(components, key=lambda item: item.dataset_id))
        if len({component.dataset_id for component in components}) != len(components):
            raise ConflictError("Composition datasets must be unique")
        owners = {dataset.id: dataset.store for dataset in self._registry.datasets}
        component_stores: set[str] = set()
        accepted: dict[str, dict[str, Mapping[str, Any]]] = {}
        component_receipts: list[dict[str, Any]] = []
        warnings: set[str] = set()
        cutoff = TemporalValue.parse(context.cutoff, pointer="/cutoff")
        for component in ordered_components:
            if owners.get(component.dataset_id) != component.store_role:
                raise ConflictError("Composition component ownership conflicts with registry")
            component_stores.add(component.store_role)
            if len(component.rows) > context.component_limit:
                raise ResourceLimitError("Composition component exceeded its pre-join bound")
            by_identity: dict[str, Mapping[str, Any]] = {}
            for row in component.rows:
                decision = availability_at_or_before(
                    TemporalValue.parse(row.available_at, pointer="/available_at"),
                    cutoff,
                    context.date_only_policy,
                )
                warnings.update(decision.warnings)
                if not decision.included:
                    continue
                if row.join_identity in by_identity:
                    raise ConflictError("Composition component has an ambiguous stable identity")
                by_identity[row.join_identity] = dict(row.payload)
            accepted[component.dataset_id] = by_identity
            component_receipts.append(
                {
                    "dataset_id": component.dataset_id,
                    "store_role": component.store_role,
                    "store_receipt": dict(component.store_receipt),
                    "accepted_count": len(by_identity),
                }
            )
        if len(component_stores) < 2:
            raise ValidationError("Composition must span at least two operational stores")

        dataset_order = tuple(sorted(accepted))
        identities = sorted(
            {identity for rows in accepted.values() for identity in rows}
        )
        truncated = len(identities) > context.joined_limit
        selected_identities = identities[: context.joined_limit]
        joined = []
        for identity in selected_identities:
            values = {
                dataset_id: accepted[dataset_id].get(identity)
                for dataset_id in dataset_order
            }
            joined.append(
                {
                    "join_identity": identity,
                    "components": values,
                    "missing_datasets": [
                        dataset_id
                        for dataset_id in dataset_order
                        if values[dataset_id] is None
                    ],
                }
            )
        payload: dict[str, Any] = {
            "contract": "quant_data.cross_store_composition",
            "contract_version": "1.0.0",
            "consistency": "best_effort_multi_store",
            "cutoff": context.cutoff,
            "date_only_policy": context.date_only_policy.value,
            "component_limit": context.component_limit,
            "joined_limit": context.joined_limit,
            "components": component_receipts,
            "rows": joined,
            "warnings": sorted(warnings),
            "truncated": truncated,
        }
        payload["sha256"] = hashlib.sha256(
            dumps_strict(payload).encode("utf-8")
        ).hexdigest()
        return payload


def read_ingestion_run_component(
    store_map: StoreMap,
    registry: Registry,
    *,
    dataset_id: str,
    context: CompositionContext,
) -> ComponentBatch:
    """Read a bounded registered snapshot stream under one SQLite snapshot.

    This foundation adapter composes capture/snapshot evidence, not domain
    observations.  It therefore requires ``captured_at`` to be a declared
    availability field and never substitutes ingestion completion time.
    """

    dataset = next(
        (item for item in registry.datasets if item.id == dataset_id and item.active),
        None,
    )
    if dataset is None:
        raise ValidationError("Unknown or inactive composition dataset")
    availability_fields = dataset.temporal.get("availability_fields")
    if (
        not isinstance(availability_fields, list)
        or "captured_at" not in availability_fields
    ):
        raise ValidationError(
            "Dataset does not declare captured_at as a composition availability field"
        )
    with dataset_read_connection(store_map, registry, dataset_id) as connection:
        migration_rows = [
            {"id": str(row["migration_id"]), "sha256": str(row["sha256"])}
            for row in connection.execute(
                "SELECT migration_id, sha256 FROM schema_migrations ORDER BY ordinal"
            )
        ]

        cutoff = TemporalValue.parse(context.cutoff, pointer="/cutoff")

        def read_eligible_rows() -> list[Any]:
            raw_rows = list(
                connection.execute(
                    """
                    SELECT run.run_id, run.semantic_identity, run.completed_at,
                           run.artifact_id, run.snapshot_id, run.fetched_count,
                           run.written_count, snapshot.captured_at,
                           snapshot.captured_precision
                    FROM ingestion_runs AS run
                    JOIN ingestion_snapshots AS snapshot
                      ON snapshot.run_id = run.run_id
                     AND snapshot.dataset_id = run.dataset_id
                    WHERE run.dataset_id=? AND run.status='succeeded'
                      AND snapshot.validation_state='validated'
                    ORDER BY run.semantic_identity, run.run_id
                    LIMIT ?
                    """,
                    (dataset_id, context.component_limit + 1),
                )
            )
            if len(raw_rows) > context.component_limit:
                raise ResourceLimitError(
                    "Registered snapshot stream exceeds its component bound"
                )
            return [
                row
                for row in raw_rows
                if availability_at_or_before(
                    TemporalValue.parse(str(row["captured_at"]), pointer="/captured_at"),
                    cutoff,
                    context.date_only_policy,
                ).included
            ]

        def read_state(selected_rows: list[Any]) -> dict[str, Any]:
            material = {
                "migrations": migration_rows,
                "row_count": len(selected_rows),
                "capture_high_water": max(
                    (str(row["captured_at"]) for row in selected_rows),
                    default=None,
                ),
                "snapshot_high_water": max(
                    (str(row["snapshot_id"]) for row in selected_rows),
                    default=None,
                ),
            }
            return {
                **material,
                "sha256": hashlib.sha256(
                    dumps_strict(material).encode("utf-8")
                ).hexdigest(),
            }

        selected_rows = read_eligible_rows()
        read_start = read_state(selected_rows)
        read_completion = read_state(read_eligible_rows())
        if read_start["sha256"] != read_completion["sha256"]:
            raise ConflictError("Store state changed inside a composition read snapshot")
    rows = tuple(
        ComponentRow(
            join_identity=str(row["semantic_identity"]),
            available_at=str(row["captured_at"]),
            payload={
                "run_id": str(row["run_id"]),
                "artifact_id": str(row["artifact_id"]),
                "snapshot_id": str(row["snapshot_id"]),
                "fetched_count": int(row["fetched_count"]),
                "written_count": int(row["written_count"]),
                "captured_precision": str(row["captured_precision"]),
                "completed_at": str(row["completed_at"]),
            },
        )
        for row in selected_rows
    )
    receipt_material = {
        "role": dataset.store,
        "migrations": migration_rows,
        "availability_field": "captured_at",
        "availability_basis": dataset.temporal["history_basis"],
        "cutoff": context.cutoff,
        "date_only_policy": context.date_only_policy.value,
        "read_snapshot": "sqlite_read_transaction",
        "read_start_state_sha256": read_start["sha256"],
        "read_completion_state_sha256": read_completion["sha256"],
        "cohort_evidence": "none_best_effort",
        "run_ids": [row.payload["run_id"] for row in rows],
        "snapshot_ids": [row.payload["snapshot_id"] for row in rows],
    }
    receipt = {
        **receipt_material,
        "sha256": hashlib.sha256(
            dumps_strict(receipt_material).encode("utf-8")
        ).hexdigest(),
    }
    return ComponentBatch(
        dataset_id=dataset_id,
        store_role=dataset.store,
        rows=rows,
        store_receipt=receipt,
    )
