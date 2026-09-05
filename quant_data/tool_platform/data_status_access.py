"""Public retained dataset-status adapter.

This is a read-only projection of facts already retained in the four canonical
stores.  It does not contact providers, resolve credentials, inspect scheduler
processes, or claim that an external feed is currently healthy.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Final

from quant_data.contracts import TruncationV1, WarningV1
from quant_data.data_status import data_status_snapshot, registry_data_status_targets
from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.json_codec import dumps_strict
from quant_data.registry import Registry
from quant_data.temporal import TemporalPrecision, TemporalValue

from .arguments import DatasetStatusArgumentsV1
from .context import ToolExecutionContext
from .results import (
    DiagnosticV1,
    QueryResult,
    Scalar,
    fields_from_mapping,
    records_from_mappings,
)


TOOL_NAME: Final = "data.get_dataset_status"
OPERATION_VERSION: Final = "1.0.0"


def _scalar_fields(value: Mapping[str, object]) -> dict[str, Scalar]:
    rendered: dict[str, Scalar] = {}
    for name, item in value.items():
        if isinstance(item, (Mapping, list, tuple)):
            rendered[name] = dumps_strict(item)
        elif isinstance(item, (str, int, bool, Decimal, type(None))):
            rendered[name] = item
        else:
            raise ValidationError("Dataset status produced an unsupported field")
    return rendered


def invoke_dataset_status(
    name: str,
    arguments: object,
    context: ToolExecutionContext,
    registry: Registry,
) -> QueryResult:
    """Return bounded retained status through the host-selected store map."""

    if name != TOOL_NAME:
        raise LookupError("Dataset-status operation is not registered")
    if not isinstance(arguments, DatasetStatusArgumentsV1):
        raise ValidationError("Dataset status requires typed arguments")
    if not isinstance(registry, Registry):
        raise ValidationError("Dataset-status registry is invalid")
    context.checkpoint()
    targets = registry_data_status_targets(registry)
    context.budget.require(rows=arguments.limit, series=0, operations=len(targets))
    evaluated_at = TemporalValue.parse(
        context.clock.instant(), pointer="/execution_clock"
    )
    if (
        evaluated_at.precision is not TemporalPrecision.DATETIME
        or not isinstance(evaluated_at.value, datetime)
    ):
        raise ValidationError("Dataset-status execution clock is invalid")
    snapshot = data_status_snapshot(
        context.store_map,
        targets=targets,
        registry=registry,
        now=evaluated_at.value,
    )
    raw_records = snapshot.get("records")
    if not isinstance(raw_records, list) or any(
        not isinstance(item, Mapping) for item in raw_records
    ):
        raise ValidationError("Dataset-status snapshot is invalid")
    filtered = [
        item
        for item in raw_records
        if (not arguments.stores or item.get("store") in arguments.stores)
        and (
            not arguments.dataset_ids
            or item.get("dataset_id") in arguments.dataset_ids
            or item.get("id") in arguments.dataset_ids
        )
        and (not arguments.statuses or item.get("status") in arguments.statuses)
    ]
    selected = filtered[: arguments.limit]
    if len(selected) > arguments.limit:  # pragma: no cover - defensive invariant
        raise ResourceLimitError("Dataset-status selection exceeded its limit")
    records = records_from_mappings(
        "retained_dataset_status",
        tuple(_scalar_fields(item) for item in selected),
    )
    context.checkpoint()
    return QueryResult(
        tool=name,
        status="ok",
        records=records,
        diagnostics=(
            DiagnosticV1(
                code="retained_dataset_status",
                message=(
                    "Status was derived only from retained canonical-store "
                    "control-plane evidence and declared freshness thresholds."
                ),
                metrics=fields_from_mapping(
                    {
                        "evaluated_at": snapshot.get("evaluated_at"),
                        "matched_count": len(filtered),
                        "returned_count": len(records),
                        "scope": "retained_data_only",
                    }
                ),
            ),
        ),
        warnings=(
            WarningV1(
                code="not_live_operational_health",
                message=(
                    "This result does not test providers, credentials, "
                    "scheduler processes, or perform a fetch."
                ),
            ),
        ),
        truncation=TruncationV1(
            applied=len(filtered) > arguments.limit,
            limit=arguments.limit,
            returned_count=len(records),
            total_known_count=len(filtered),
            has_more=len(filtered) > arguments.limit,
        ),
    )


__all__ = ("OPERATION_VERSION", "TOOL_NAME", "invoke_dataset_status")
