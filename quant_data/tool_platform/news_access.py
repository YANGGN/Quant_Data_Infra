"""Typed v2 adapter for the retained current FMP stock-news feed."""

from __future__ import annotations

from typing import Final, Mapping

from quant_data.contracts import LineageRef, TruncationV1, WarningV1
from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.json_codec import dumps_strict
from quant_data.news.current_repository import (
    CURRENT_NEWS_ARTICLES_DATASET_ID,
    CURRENT_NEWS_EVIDENCE_DATASET_ID,
    CurrentNewsQuery,
    CurrentNewsRepository,
    CurrentNewsSelection,
)
from quant_data.registry import Registry

from .arguments import CurrentNewsSearchArgumentsV2
from .context import ToolExecutionContext
from .results import DiagnosticV1, QueryResult, Scalar, fields_from_mapping, records_from_mappings


TOOL_NAME: Final = "news.search"
OPERATION_VERSION: Final = "2.0.0"


def _query(arguments: CurrentNewsSearchArgumentsV2) -> CurrentNewsQuery:
    return CurrentNewsQuery(
        query=arguments.query,
        symbols=arguments.symbols or None,
        mode=arguments.mode,
        as_of=arguments.as_of,
        date_only_policy=arguments.date_only_policy,
        start_date=arguments.start_date,
        end_date=arguments.end_date,
        limit=arguments.limit,
    )


def _record_fields(row: Mapping[str, object]) -> dict[str, Scalar]:
    return {
        "article_id": row["article_id"],
        "article_version_id": row["article_version_id"],
        "available_at": row["available_at"],
        "capture_id": row["capture_id"],
        "headline": row["headline"],
        "published_date_raw": row["published_date_raw"],
        "published_normalized_at": row["published_normalized_at"],
        "published_precision": row["published_precision"],
        "source_name": row["source_name"],
        "source_row": row["source_row"],
        "source_url": row["source_url"],
        "symbol": row["symbol"],
        "version_sequence": row["version_sequence"],
    }


def _lineage(selection: CurrentNewsSelection) -> tuple[LineageRef, ...]:
    capture_ids = sorted({str(row["capture_id"]) for row in selection.records})
    article_rows = {
        str(row["article_version_id"]): row for row in selection.records
    }
    evidence = tuple(
        LineageRef(
            dataset_id=CURRENT_NEWS_EVIDENCE_DATASET_ID,
            store_role="news",
            semantic_id=capture_id,
            evidence_id=capture_id,
            snapshot_id=capture_id,
        )
        for capture_id in capture_ids
    )
    articles = tuple(
        LineageRef(
            dataset_id=CURRENT_NEWS_ARTICLES_DATASET_ID,
            store_role="news",
            semantic_id=article_version_id,
            evidence_id=str(article_rows[article_version_id]["capture_id"]),
            snapshot_id=str(article_rows[article_version_id]["capture_id"]),
            canonical_version_id=article_version_id,
        )
        for article_version_id in sorted(article_rows)
    )
    return evidence + articles


def _warnings(selection: CurrentNewsSelection) -> tuple[WarningV1, ...]:
    return tuple(
        WarningV1(code=code, message=f"Current FMP news selection warning: {code}.")
        for code in selection.warnings
    )


def _result(
    *,
    name: str,
    query: CurrentNewsQuery,
    selection: CurrentNewsSelection,
) -> QueryResult:
    records = records_from_mappings(
        "current_news_headline",
        tuple(_record_fields(row) for row in selection.records),
    )
    cutoff_precision = None if query.cutoff is None else query.cutoff.precision.value
    return QueryResult(
        tool=name,
        status="ok",
        records=records,
        diagnostics=(
            DiagnosticV1(
                code="current_fmp_news_selection",
                message=(
                    "Retained FMP stock-news headline metadata was selected "
                    "without provider access."
                ),
                metrics=fields_from_mapping(
                    {
                        "availability_basis": "local_capture",
                        "cutoff": query.as_of,
                        "cutoff_precision": cutoff_precision,
                        "date_only_policy": query.date_only_policy.value,
                        "migration_ids": dumps_strict(
                            list(selection.migration_ids)
                        ),
                        "mode": query.mode,
                        "receipt_sha256": selection.receipt_sha256,
                        "returned_count": len(records),
                        "total_selected_count": selection.total_selected_count,
                        "truncated": selection.truncated,
                    }
                ),
            ),
        ),
        warnings=_warnings(selection),
        lineage=_lineage(selection),
        truncation=TruncationV1(
            applied=selection.truncated,
            limit=query.limit,
            returned_count=len(records),
            total_known_count=selection.total_selected_count,
            has_more=selection.truncated,
        ),
    )


def invoke_news_search_v2(
    name: str,
    arguments: object,
    context: ToolExecutionContext,
    registry: Registry,
) -> QueryResult:
    """Select current FMP headline metadata through the host news store route."""

    if name != TOOL_NAME:
        raise LookupError("Current-news operation is not registered")
    if not isinstance(arguments, CurrentNewsSearchArgumentsV2):
        raise ValidationError("Current-news search requires typed v2 arguments")
    if not isinstance(registry, Registry):
        raise ValidationError("Current-news search registry is invalid")
    context.checkpoint()
    query = _query(arguments)
    context.budget.require(
        rows=query.limit,
        series=0,
        operations=query.limit,
    )
    selection = CurrentNewsRepository(context.store_map, registry).search(query)
    if len(selection.records) > query.limit:
        raise ResourceLimitError("Current-news selection exceeded its limit")
    context.checkpoint()
    return _result(name=name, query=query, selection=selection)


__all__ = (
    "OPERATION_VERSION",
    "TOOL_NAME",
    "invoke_news_search_v2",
)
