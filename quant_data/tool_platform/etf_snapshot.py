"""Public ETF snapshot adapter over the retained market domain."""
from __future__ import annotations

from quant_data.contracts import TruncationV1, WarningV1
from quant_data.errors import ValidationError
from quant_data.market.etf_snapshot import retained_etf_snapshot
from quant_data.registry import Registry
from .arguments import EtfAllocatorSnapshotArgumentsV1
from .context import ToolExecutionContext
from .results import DiagnosticV1, QueryResult, fields_from_mapping, records_from_mappings

TOOL_NAME = "portfolio.get_etf_allocator_snapshot"


def invoke_etf_snapshot(
    name: str, arguments: EtfAllocatorSnapshotArgumentsV1,
    context: ToolExecutionContext, registry: Registry,
) -> QueryResult:
    if name != TOOL_NAME or not isinstance(arguments, EtfAllocatorSnapshotArgumentsV1):
        raise ValidationError("ETF snapshot requires its registered typed arguments")
    context.checkpoint()
    context.budget.require(rows=len(arguments.symbols) * 400, operations=5_000_000)
    snapshot = retained_etf_snapshot(
        context.store_map, registry, arguments.symbols, arguments.decision_as_of,
        now=context.clock.instant(), checkpoint=context.checkpoint,
        tool_version=context.tool_version,
    )
    context.checkpoint()
    provider_close = context.tool_version == "2.0.0"
    warnings = [WarningV1(
        code="etf_historical_reconstruction_not_established",
        message="Cutoff-filtered local captures do not certify original publication or historical adjustment knowledge.",
    )]
    if not provider_close:
        warnings.insert(0, WarningV1(
            code="etf_split_adjustment_not_established",
            message="Provider-native prices cannot be relabelled as split-adjusted excluding distributions.",
        ))
    elif any(row["source_adjustment_status"] != "split_adjusted_excluding_distributions"
             for row in snapshot.rows):
        warnings.insert(0, WarningV1(
            code="etf_close_binding_incomplete",
            message="Some requested symbols lack prices bound to the documented FMP full-EOD close.",
        ))
    if provider_close and any(row["adjustment_vintage_status"] == "mixed_provider_captures"
                              for row in snapshot.rows):
        warnings.append(WarningV1(
            code="etf_mixed_provider_adjustment_vintages",
            message="Retained prices span multiple captures; a common provider adjustment vintage is not certified.",
        ))
    return QueryResult(
        tool=name, status=("ok" if provider_close and snapshot.summary["complete"] else "not_established"),
        records=records_from_mappings("etf_allocator_features", snapshot.rows),
        diagnostics=(DiagnosticV1(
            code="etf_allocator_snapshot",
            message=("FMP split-only close is used unchanged; inspect per-feature readiness and capture limitations."
                     if provider_close else
                     "Retained input coverage is reported; split-only feature readiness is not established."),
            metrics=fields_from_mapping(snapshot.summary),
        ),),
        warnings=tuple(warnings),
        lineage=snapshot.lineage,
        truncation=TruncationV1(applied=False, limit=len(arguments.symbols),
            returned_count=len(snapshot.rows), total_known_count=len(arguments.symbols),
            has_more=False),
    )
