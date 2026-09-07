"""Typed v2 adapters for retained current news."""

from __future__ import annotations

import base64
import binascii
import hashlib
from datetime import datetime, timezone
from typing import Final, Mapping

from quant_data.contracts import LineageRef, TruncationV1, WarningV1
from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.news.current_multi_source import CURRENT_MULTI_SOURCE_FEED_IDS
from quant_data.news.current_multi_source_repository import (
    CURRENT_MULTI_SOURCE_REPOSITORY_ARTICLES_DATASET_ID,
    CURRENT_MULTI_SOURCE_REPOSITORY_EVIDENCE_DATASET_ID,
    CurrentMultiSourceNewsRepository,
)
from quant_data.news.current_repository import (
    CURRENT_NEWS_ARTICLES_DATASET_ID,
    CURRENT_NEWS_EVIDENCE_DATASET_ID,
    CurrentNewsKeysetAnchor,
    CurrentNewsQuery,
    CurrentNewsRepository,
    CurrentNewsSelection,
)
from quant_data.registry import Registry

from .arguments import (
    CurrentNewsSearchArgumentsV2,
    CurrentNewsSearchArgumentsV21,
    CurrentNewsSearchArgumentsV22,
    CurrentNewsSearchArgumentsV23,
)
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


FMP_CURRENT_SOURCE_ID: Final = "fmp_stock_latest"
MULTI_SOURCE_OPERATION_VERSION: Final = "2.1.0"
_V21_SOURCE_IDS: Final = frozenset(
    (FMP_CURRENT_SOURCE_ID, *CURRENT_MULTI_SOURCE_FEED_IDS)
)


def _source_ids_v21(arguments: CurrentNewsSearchArgumentsV21) -> tuple[str, ...]:
    source_ids = arguments.source_ids
    if len(source_ids) != len(set(source_ids)):
        raise ValidationError("Current-news source_ids must be distinct")
    if any(source_id not in _V21_SOURCE_IDS for source_id in source_ids):
        raise ValidationError("Current-news source_ids contain an unsupported feed")
    return source_ids


def _instant_v21(value: object, *, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"Current-news {label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"Current-news {label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValidationError(f"Current-news {label} must include an offset")
    return parsed.astimezone(timezone.utc)


def _normalize_fmp_v21(row: Mapping[str, object]) -> dict[str, object]:
    symbol = row["symbol"]
    return {
        **row,
        "feed_id": FMP_CURRENT_SOURCE_ID,
        "provider": "fmp",
        "published_offset_status": row["published_offset_status"],
        "summary": "",
        "symbols": [symbol],
        "_family": "fmp",
    }


def _sort_v21(records: list[dict[str, object]]) -> None:
    records.sort(key=lambda row: str(row["article_id"]))
    records.sort(
        key=lambda row: _instant_v21(
            row["available_at"], label="availability"
        ),
        reverse=True,
    )
    records.sort(
        key=lambda row: (
            row["published_normalized_at"] is not None,
            _instant_v21(
                row["published_normalized_at"], label="publication"
            )
            if row["published_normalized_at"] is not None
            else datetime.min.replace(tzinfo=timezone.utc),
        ),
        reverse=True,
    )


def _record_fields_v21(row: Mapping[str, object]) -> dict[str, Scalar]:
    return {
        "article_id": row["article_id"],
        "article_version_id": row["article_version_id"],
        "available_at": row["available_at"],
        "capture_id": row["capture_id"],
        "feed_id": row["feed_id"],
        "headline": row["headline"],
        "provider": row["provider"],
        "published_date_raw": row["published_date_raw"],
        "published_normalized_at": row["published_normalized_at"],
        "published_offset_status": row["published_offset_status"],
        "published_precision": row["published_precision"],
        "source_name": row["source_name"],
        "source_row": row["source_row"],
        "source_url": row["source_url"],
        "summary": row["summary"],
        "symbol": row["symbol"],
        "symbols": dumps_strict(row["symbols"]),
        "version_sequence": row["version_sequence"],
    }


def _lineage_v21(
    records: tuple[dict[str, object], ...],
) -> tuple[LineageRef, ...]:
    evidence_keys: set[tuple[str, str]] = set()
    article_keys: set[tuple[str, str, str]] = set()
    for row in records:
        if row["_family"] == "fmp":
            evidence_dataset = CURRENT_NEWS_EVIDENCE_DATASET_ID
            articles_dataset = CURRENT_NEWS_ARTICLES_DATASET_ID
        else:
            evidence_dataset = (
                CURRENT_MULTI_SOURCE_REPOSITORY_EVIDENCE_DATASET_ID
            )
            articles_dataset = (
                CURRENT_MULTI_SOURCE_REPOSITORY_ARTICLES_DATASET_ID
            )
        capture_id = str(row["capture_id"])
        version_id = str(row["article_version_id"])
        evidence_keys.add((evidence_dataset, capture_id))
        article_keys.add((articles_dataset, capture_id, version_id))
    evidence = tuple(
        LineageRef(
            dataset_id=dataset_id,
            store_role="news",
            semantic_id=capture_id,
            evidence_id=capture_id,
            snapshot_id=capture_id,
        )
        for dataset_id, capture_id in sorted(evidence_keys)
    )
    articles = tuple(
        LineageRef(
            dataset_id=dataset_id,
            store_role="news",
            semantic_id=version_id,
            evidence_id=capture_id,
            snapshot_id=capture_id,
            canonical_version_id=version_id,
        )
        for dataset_id, capture_id, version_id in sorted(article_keys)
    )
    return evidence + articles


def _result_v21(
    *,
    name: str,
    query: CurrentNewsQuery,
    source_ids: tuple[str, ...],
    records: tuple[dict[str, object], ...],
    total_selected_count: int,
    migration_ids: tuple[str, ...],
    receipt_sha256: str,
    warnings: tuple[str, ...],
    next_cursor: str | None = None,
    record_kind: str = "current_news_headline_v2_1",
) -> QueryResult:
    rendered = records_from_mappings(
        record_kind,
        tuple(_record_fields_v21(row) for row in records),
    )
    truncated = total_selected_count > query.limit
    cutoff_precision = (
        None if query.cutoff is None else query.cutoff.precision.value
    )
    return QueryResult(
        tool=name,
        status="ok",
        records=rendered,
        diagnostics=(
            DiagnosticV1(
                code="current_multi_source_news_selection",
                message=(
                    "Retained fixed-source headline metadata was selected "
                    "without provider access."
                ),
                metrics=fields_from_mapping(
                    {
                        "availability_basis": "local_capture",
                        "cutoff": query.as_of,
                        "cutoff_precision": cutoff_precision,
                        "date_only_policy": query.date_only_policy.value,
                        "migration_ids": dumps_strict(list(migration_ids)),
                        "mode": query.mode,
                        "receipt_sha256": receipt_sha256,
                        "returned_count": len(rendered),
                        "source_ids": dumps_strict(list(source_ids)),
                        "total_selected_count": total_selected_count,
                        "truncated": truncated,
                    }
                ),
            ),
        ),
        warnings=tuple(
            WarningV1(
                code=code,
                message=f"Current multi-source news warning: {code}.",
            )
            for code in warnings
        ),
        lineage=_lineage_v21(records),
        truncation=TruncationV1(
            applied=truncated,
            limit=query.limit,
            returned_count=len(rendered),
            total_known_count=total_selected_count,
            has_more=truncated,
            next_cursor=next_cursor,
        ),
    )


def invoke_news_search_v21(
    name: str,
    arguments: object,
    context: ToolExecutionContext,
    registry: Registry,
) -> QueryResult:
    """Select retained fixed-source headline metadata through the news store."""

    if name != TOOL_NAME:
        raise LookupError("Current multi-source news operation is not registered")
    if not isinstance(arguments, CurrentNewsSearchArgumentsV21):
        raise ValidationError(
            "Current multi-source news search requires typed v2.1 arguments"
        )
    if not isinstance(registry, Registry):
        raise ValidationError("Current multi-source news registry is invalid")

    source_ids = _source_ids_v21(arguments)
    query = _query(arguments)
    context.checkpoint()
    context.budget.require(
        rows=query.limit,
        series=0,
        operations=query.limit * 2,
    )

    include_fmp = not source_ids or FMP_CURRENT_SOURCE_ID in source_ids
    selected_multi_ids = tuple(
        source_id
        for source_id in source_ids
        if source_id != FMP_CURRENT_SOURCE_ID
    )
    include_multi = not source_ids or bool(selected_multi_ids)
    fmp_selection = (
        CurrentNewsRepository(context.store_map, registry).search(query)
        if include_fmp
        else None
    )
    multi_selection = (
        CurrentMultiSourceNewsRepository(
            context.store_map, registry
        ).search(
            query,
            source_ids=selected_multi_ids if source_ids else CURRENT_MULTI_SOURCE_FEED_IDS,
        )
        if include_multi
        else None
    )

    records: list[dict[str, object]] = []
    if fmp_selection is not None:
        records.extend(
            _normalize_fmp_v21(row) for row in fmp_selection.records
        )
    if multi_selection is not None:
        records.extend(
            {**row, "_family": "multi"}
            for row in multi_selection.records
        )
    _sort_v21(records)
    selected_records = tuple(records[: query.limit])
    total_selected_count = sum(
        selection.total_selected_count
        for selection in (fmp_selection, multi_selection)
        if selection is not None
    )
    migration_ids = tuple(
        sorted(
            {
                migration_id
                for selection in (fmp_selection, multi_selection)
                if selection is not None
                for migration_id in selection.migration_ids
            }
        )
    )
    warnings = tuple(
        sorted(
            {
                warning
                for selection in (fmp_selection, multi_selection)
                if selection is not None
                for warning in selection.warnings
            }
        )
    )
    subreceipts = [
        selection.receipt_sha256
        for selection in (fmp_selection, multi_selection)
        if selection is not None
    ]
    receipt_sha256 = hashlib.sha256(
        dumps_strict(
            {
                "request": query.receipt_mapping(),
                "selected_article_versions": [
                    str(row["article_version_id"])
                    for row in selected_records
                ],
                "source_ids": list(source_ids),
                "subreceipts": subreceipts,
                "total_selected_count": total_selected_count,
            }
        ).encode("utf-8")
    ).hexdigest()
    context.checkpoint()
    return _result_v21(
        name=name,
        query=query,
        source_ids=source_ids,
        records=selected_records,
        total_selected_count=total_selected_count,
        migration_ids=migration_ids,
        receipt_sha256=receipt_sha256,
        warnings=warnings,
    )


PAGINATED_OPERATION_VERSION: Final = "2.2.0"
_CURSOR_CONTRACT: Final = "quant_data.current_news_cursor"
_CURSOR_VERSION: Final = 1


def _cursor_query_sha256(
    arguments: CurrentNewsSearchArgumentsV22,
    source_ids: tuple[str, ...],
) -> str:
    material = {
        "as_of": arguments.as_of,
        "date_only_policy": arguments.date_only_policy,
        "end_date": arguments.end_date,
        "mode": arguments.mode,
        "query": arguments.query,
        "source_ids": sorted(source_ids),
        "start_date": arguments.start_date,
        "symbols": sorted(arguments.symbols),
    }
    return hashlib.sha256(dumps_strict(material).encode("utf-8")).hexdigest()


def _encode_cursor(
    anchor: CurrentNewsKeysetAnchor,
    *,
    query_sha256: str,
) -> str:
    payload = dumps_strict(
        {
            "anchor": anchor.receipt_mapping(),
            "contract": _CURSOR_CONTRACT,
            "query_sha256": query_sha256,
            "version": _CURSOR_VERSION,
        }
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _cursor_datetime(value: object, *, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"Current-news cursor {label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"Current-news cursor {label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValidationError(f"Current-news cursor {label} is invalid")
    return parsed.astimezone(timezone.utc)


def _decode_cursor(
    cursor: str | None,
    *,
    query_sha256: str,
) -> CurrentNewsKeysetAnchor | None:
    if cursor is None:
        return None
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.b64decode(
            cursor + padding,
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, binascii.Error) as exc:
        raise ValidationError("Current-news cursor is invalid") from exc
    parsed = loads_strict(raw, max_bytes=4_096)
    if not isinstance(parsed, Mapping) or set(parsed) != {
        "anchor",
        "contract",
        "query_sha256",
        "version",
    }:
        raise ValidationError("Current-news cursor is invalid")
    if (
        parsed["contract"] != _CURSOR_CONTRACT
        or parsed["version"] != _CURSOR_VERSION
        or parsed["query_sha256"] != query_sha256
    ):
        raise ValidationError("Current-news cursor does not match this query")
    anchor = parsed["anchor"]
    if not isinstance(anchor, Mapping) or set(anchor) != {
        "article_id",
        "captured_at",
        "publication_instant",
    }:
        raise ValidationError("Current-news cursor anchor is invalid")
    article_id = anchor["article_id"]
    publication = anchor["publication_instant"]
    if not isinstance(article_id, str) or not article_id:
        raise ValidationError("Current-news cursor article identity is invalid")
    if publication is not None:
        publication = _cursor_datetime(publication, label="publication instant")
    decoded = CurrentNewsKeysetAnchor(
        publication_instant=publication,
        captured_at=_cursor_datetime(anchor["captured_at"], label="capture instant"),
        article_id=article_id,
    )
    if _encode_cursor(decoded, query_sha256=query_sha256) != cursor:
        raise ValidationError("Current-news cursor is not canonical")
    return decoded


def invoke_news_search_v22(name, arguments, context, registry) -> QueryResult:
    return _invoke_news_search_page(name, arguments, context, registry, include_websites=False)


def invoke_news_search_v23(name, arguments, context, registry) -> QueryResult:
    return _invoke_news_search_page(name, arguments, context, registry, include_websites=True)


def _invoke_news_search_page(
    name: str,
    arguments: object,
    context: ToolExecutionContext,
    registry: Registry,
    *,
    include_websites: bool,
) -> QueryResult:
    """Select one keyset-paginated page of retained fixed-source news."""

    if name != TOOL_NAME:
        raise LookupError("Current multi-source news operation is not registered")
    argument_type = CurrentNewsSearchArgumentsV23 if include_websites else CurrentNewsSearchArgumentsV22
    if type(arguments) is not argument_type:
        raise ValidationError(
            "Current multi-source news search requires matching typed arguments"
        )
    if not isinstance(registry, Registry):
        raise ValidationError("Current multi-source news registry is invalid")

    if include_websites:
        allowed = (*sorted(_V21_SOURCE_IDS), "finviz", "financialjuice")
        source_ids = arguments.source_ids or allowed
        if len(set(source_ids)) != len(source_ids) or any(s not in allowed for s in source_ids):
            raise ValidationError("Current-news source_ids contain an unsupported or duplicate feed")
    else:
        source_ids = _source_ids_v21(arguments)
    query_sha256 = _cursor_query_sha256(arguments, source_ids)
    after = _decode_cursor(arguments.cursor, query_sha256=query_sha256)
    query = _query(arguments)
    context.checkpoint()
    context.budget.require(rows=query.limit, series=0, operations=query.limit * 2)

    include_fmp = not source_ids or FMP_CURRENT_SOURCE_ID in source_ids
    selected_multi_ids = tuple(
        source_id for source_id in source_ids if source_id != FMP_CURRENT_SOURCE_ID
    )
    include_multi = not source_ids or bool(selected_multi_ids)
    fmp_selection = (
        CurrentNewsRepository(context.store_map, registry).search(query, after=after)
        if include_fmp
        else None
    )
    multi_selection = (
        CurrentMultiSourceNewsRepository(context.store_map, registry).search(
            query,
            source_ids=selected_multi_ids if source_ids else CURRENT_MULTI_SOURCE_FEED_IDS,
            after=after,
        )
        if include_multi
        else None
    )

    records: list[dict[str, object]] = []
    if fmp_selection is not None:
        records.extend(_normalize_fmp_v21(row) for row in fmp_selection.records)
    if multi_selection is not None:
        records.extend({**row, "_family": "multi"} for row in multi_selection.records)
    _sort_v21(records)
    selected_records = tuple(records[: query.limit])
    total_selected_count = sum(
        selection.total_selected_count
        for selection in (fmp_selection, multi_selection)
        if selection is not None
    )
    migration_ids = tuple(
        sorted(
            {
                migration_id
                for selection in (fmp_selection, multi_selection)
                if selection is not None
                for migration_id in selection.migration_ids
            }
        )
    )
    warnings = tuple(
        sorted(
            {
                warning
                for selection in (fmp_selection, multi_selection)
                if selection is not None
                for warning in selection.warnings
            }
        )
    )
    subreceipts = [
        selection.receipt_sha256
        for selection in (fmp_selection, multi_selection)
        if selection is not None
    ]
    has_more = total_selected_count > query.limit
    next_cursor = (
        _encode_cursor(
            CurrentNewsKeysetAnchor.from_record(selected_records[-1]),
            query_sha256=query_sha256,
        )
        if has_more and selected_records
        else None
    )
    receipt_sha256 = hashlib.sha256(
        dumps_strict(
            {
                "after": None if after is None else after.receipt_mapping(),
                "query_sha256": query_sha256,
                "request": query.receipt_mapping(),
                "selected_article_versions": [
                    str(row["article_version_id"]) for row in selected_records
                ],
                "source_ids": list(source_ids),
                "subreceipts": subreceipts,
                "total_selected_count": total_selected_count,
            }
        ).encode("utf-8")
    ).hexdigest()
    context.checkpoint()
    return _result_v21(
        name=name,
        query=query,
        source_ids=source_ids,
        records=selected_records,
        total_selected_count=total_selected_count,
        migration_ids=migration_ids,
        receipt_sha256=receipt_sha256,
        warnings=warnings,
        next_cursor=next_cursor,
        record_kind="current_news_headline_v2_3" if include_websites else "current_news_headline_v2_2",
    )

__all__ = (
    "FMP_CURRENT_SOURCE_ID",
    "MULTI_SOURCE_OPERATION_VERSION",
    "OPERATION_VERSION",
    "PAGINATED_OPERATION_VERSION",
    "TOOL_NAME",
    "invoke_news_search_v2",
    "invoke_news_search_v21",
    "invoke_news_search_v22",
    "invoke_news_search_v23",
)
