"""Read-only v2 company projections backed by the Stage 4 repository.

The public adapter deliberately selects a closed, reviewed share-count metric
set. It does not accept caller-selected metric identifiers or query text.
"""

from __future__ import annotations

from typing import Final, Mapping

from quant_data.company.sec_companyfacts import SEC_CORE_MAPPINGS, SecCoreMapping
from quant_data.company.stage4_repository import (
    CompanyStage4Query,
    CompanyStage4Repository,
)
from quant_data.contracts import LineageRef, TruncationV1, WarningV1
from quant_data.errors import ValidationError
from quant_data.registry import Registry
from quant_data.stores import stable_id

from .arguments import CompanyShareCountHistoryArgumentsV2
from .context import ToolExecutionContext
from .results import QueryResult, Scalar, records_from_mappings


TOOL_NAME: Final = "company.get_share_count_history"
OPERATION_VERSION: Final = "2.0.0"
_SHARE_COUNT_METRIC_CODES: Final = (
    "shares_outstanding",
    "weighted_average_shares_basic",
    "weighted_average_shares_diluted",
)
_SEC_MAPPINGS_BY_CODE: Final = {
    mapping.metric_code: mapping for mapping in SEC_CORE_MAPPINGS
}

try:
    _REVIEWED_SHARE_COUNT_MAPPINGS: Final[tuple[SecCoreMapping, ...]] = tuple(
        _SEC_MAPPINGS_BY_CODE[metric_code]
        for metric_code in _SHARE_COUNT_METRIC_CODES
    )
except KeyError as exc:  # pragma: no cover - reviewed mappings are static code.
    raise RuntimeError("Reviewed share-count mapping is unavailable") from exc

SHARE_COUNT_METRIC_IDS: Final = {
    mapping.metric_code: stable_id("company_metric", mapping.metric_code)
    for mapping in _REVIEWED_SHARE_COUNT_MAPPINGS
}
_METRIC_CODE_BY_ID: Final = {
    metric_id: metric_code
    for metric_code, metric_id in SHARE_COUNT_METRIC_IDS.items()
}
_MAPPING_BY_METRIC_CODE: Final = {
    mapping.metric_code: mapping for mapping in _REVIEWED_SHARE_COUNT_MAPPINGS
}


def _validate_arguments(
    arguments: CompanyShareCountHistoryArgumentsV2,
) -> None:
    if arguments.mode not in {"latest", "as_of"}:
        raise ValidationError("Company share-count mode is unsupported")
    if arguments.mode == "as_of" and arguments.as_of is None:
        raise ValidationError("Company share-count as_of mode requires a cutoff")
    if arguments.mode == "latest" and arguments.as_of is not None:
        raise ValidationError(
            "Company share-count latest mode does not accept a cutoff"
        )


def _metric_code_for(row: Mapping[str, object]) -> str:
    metric_id = row.get("metric_id")
    metric_code = (
        _METRIC_CODE_BY_ID.get(metric_id)
        if isinstance(metric_id, str)
        else None
    )
    if metric_code is None:
        raise ValidationError(
            "Company share-count query returned an unapproved metric"
        )
    mapping = _MAPPING_BY_METRIC_CODE[metric_code]
    if row.get("share_semantics") != mapping.share_semantics:
        raise ValidationError(
            "Company share-count semantic mapping is inconsistent"
        )
    if row.get("base_unit") != mapping.unit:
        raise ValidationError("Company share-count unit mapping is inconsistent")
    return metric_code


def _record_fields(
    row: Mapping[str, object],
    metric_code: str,
) -> dict[str, Scalar]:
    """Project all scalar Stage 4 fundamental fields available to this tool."""

    return {
        "fundamental_version_id": row.get("fundamental_version_id"),
        "metric_id": row.get("metric_id"),
        "metric_code": metric_code,
        "metric_label": row.get("metric_label"),
        "base_unit": row.get("base_unit"),
        "mapping_id": row.get("mapping_id"),
        "mapping_version": row.get("mapping_version"),
        "share_semantics": row.get("share_semantics"),
        "fiscal_year": row.get("fiscal_year"),
        "fiscal_period": row.get("fiscal_period"),
        "reference_period_start": row.get("reference_period_start"),
        "reference_period_end": row.get("reference_period_end"),
        "accession_number": row.get("accession_number"),
        "source_fact_version_id": row.get("source_fact_version_id"),
        "value": row.get("value"),
        "value_state": row.get("value_state"),
        "missing_reason": row.get("missing_reason"),
        "available_at": row.get("available_at"),
        "available_precision": row.get("available_precision"),
        "source_snapshot_id": row.get("source_snapshot_id"),
    }


def _lineage(rows: tuple[Mapping[str, object], ...]) -> tuple[LineageRef, ...]:
    return tuple(
        LineageRef(
            dataset_id="fixture.company.fundamentals",
            store_role="company",
            semantic_id=str(row["fundamental_version_id"]),
            snapshot_id=(
                str(row["source_snapshot_id"])
                if row.get("source_snapshot_id") is not None
                else None
            ),
            canonical_version_id=str(row["fundamental_version_id"]),
        )
        for row in rows
    )


def _warnings(rows: tuple[Mapping[str, object], ...]) -> tuple[WarningV1, ...]:
    messages = tuple(
        sorted(
            {
                warning
                for row in rows
                for warning in row.get("warnings", ())
                if isinstance(warning, str) and warning
            }
        )
    )
    return tuple(
        WarningV1(code="source_selection_warning", message=message)
        for message in messages
    )


def invoke_company_share_count_history(
    name: str,
    arguments: CompanyShareCountHistoryArgumentsV2,
    context: ToolExecutionContext,
    registry: Registry,
) -> QueryResult:
    """Return reviewed SEC share-count facts for one exact CIK.

    Instant shares outstanding and weighted-average basic/diluted shares remain
    separate records. Missing approved metrics stay absent; the adapter never
    derives or substitutes a share count.
    """

    if name != TOOL_NAME:
        raise LookupError("Company share-count operation is not registered")
    if not isinstance(arguments, CompanyShareCountHistoryArgumentsV2):
        raise ValidationError(
            "Company share-count history requires typed arguments"
        )
    _validate_arguments(arguments)
    context.checkpoint()
    context.budget.require(
        rows=arguments.limit,
        series=0,
        operations=arguments.limit,
    )
    query = CompanyStage4Query(
        cik=arguments.cik,
        as_of=arguments.as_of,
        date_only_policy=arguments.date_only_policy,
        limit=arguments.limit,
    )
    rows = tuple(
        CompanyStage4Repository(context.store_map, registry).get_fundamentals(
            query,
            metric_codes=tuple(SHARE_COUNT_METRIC_IDS.values()),
        )
    )
    metric_codes = tuple(_metric_code_for(row) for row in rows)
    records = records_from_mappings(
        "company_share_count",
        tuple(
            _record_fields(row, metric_code)
            for row, metric_code in zip(rows, metric_codes, strict=True)
        ),
    )
    context.checkpoint()
    return QueryResult(
        tool=name,
        records=records,
        warnings=_warnings(rows),
        lineage=_lineage(rows),
        truncation=TruncationV1(
            applied=False,
            limit=arguments.limit,
            returned_count=len(records),
            total_known_count=len(records),
            has_more=False,
        ),
    )


__all__ = (
    "OPERATION_VERSION",
    "SHARE_COUNT_METRIC_IDS",
    "TOOL_NAME",
    "invoke_company_share_count_history",
)
