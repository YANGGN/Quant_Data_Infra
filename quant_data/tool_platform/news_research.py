"""Small read-only tool adapters for retained current-news metadata."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Final

from quant_data.contracts import LineageRef, TimeSeries, TruncationV1, WarningV1
from quant_data.errors import ValidationError
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.market.stage10_series import (
    DATASET_ID as STAGE10_PRICE_DATASET_ID,
    Stage10DailyPriceQuery,
    Stage10DailyPriceRepository,
)
from quant_data.news.analytics import (
    attention_metrics,
    classify_events,
    entity_coverage,
    headline_sentiment,
    story_clusters,
)
from quant_data.news.current_multi_source_repository import (
    CURRENT_MULTI_SOURCE_REPOSITORY_ARTICLES_DATASET_ID,
    CURRENT_MULTI_SOURCE_REPOSITORY_EVIDENCE_DATASET_ID,
)
from quant_data.news.current_repository import (
    CURRENT_NEWS_ARTICLES_DATASET_ID,
    CURRENT_NEWS_EVIDENCE_DATASET_ID,
)
from quant_data.news.tool_repository import (
    FMP_STOCK_LATEST_SOURCE_ID,
    CurrentNewsToolRepository,
)
from quant_data.registry import Registry

from .analytics import event_study, return_series
from .arguments import (
    CurrentNewsSearchArgumentsV21,
    NewsAnalysisArgumentsV1,
    NewsAttentionArgumentsV1,
    NewsEventImpactArgumentsV1,
    NewsItemHistoryArgumentsV1,
    NewsSourceStatusArgumentsV1,
    NewsStoryClusterArgumentsV1,
)
from .context import ToolExecutionContext
from .news_access import invoke_news_search_v21
from .results import (
    DiagnosticV1,
    QueryResult,
    Scalar,
    fields_from_mapping,
    records_from_mappings,
    research_envelope,
)


NEWS_RESEARCH_TOOL_NAMES: Final = frozenset(
    {
        "news.get_source_status",
        "news.get_item_history",
        "news.story_clusters",
        "news.entity_coverage",
        "news.attention_metrics",
        "news.classify_events",
        "news.headline_sentiment",
        "research.news_event_impact",
    }
)


def _scalar_fields(value: Mapping[str, object]) -> dict[str, Scalar]:
    rendered: dict[str, Scalar] = {}
    for name, item in value.items():
        if isinstance(item, (Mapping, list, tuple)):
            rendered[name] = dumps_strict(item)
        elif isinstance(item, (str, int, bool, type(None))):
            rendered[name] = item
        else:
            # Decimal is accepted by the typed result contract. Keeping this
            # branch explicit prevents accidental binary-float projection.
            from decimal import Decimal

            if not isinstance(item, Decimal):
                raise ValidationError("News analysis produced an unsupported field")
            rendered[name] = item
    return rendered


def _selection_arguments(arguments: NewsAnalysisArgumentsV1) -> CurrentNewsSearchArgumentsV21:
    return CurrentNewsSearchArgumentsV21(
        query=arguments.query,
        symbols=arguments.symbols,
        mode=arguments.mode,
        as_of=arguments.as_of,
        date_only_policy=arguments.date_only_policy,
        start_date=arguments.start_date,
        end_date=arguments.end_date,
        limit=arguments.limit,
        source_ids=arguments.source_ids,
    )


def _selection_records(result: QueryResult) -> tuple[dict[str, object], ...]:
    records: list[dict[str, object]] = []
    for record in result.records:
        projected: dict[str, object] = {
            field.name: field.value for field in record.fields
        }
        symbols = projected.get("symbols")
        if isinstance(symbols, str):
            decoded = loads_strict(symbols)
            if not isinstance(decoded, list) or any(
                not isinstance(item, str) for item in decoded
            ):
                raise ValidationError("Selected current-news symbols are invalid")
            projected["symbols"] = tuple(decoded)
        records.append(projected)
    return tuple(records)


def _selected_news(
    arguments: NewsAnalysisArgumentsV1,
    context: ToolExecutionContext,
    registry: Registry,
) -> tuple[tuple[dict[str, object], ...], QueryResult]:
    result = invoke_news_search_v21(
        "news.search",
        _selection_arguments(arguments),
        context,
        registry,
    )
    return _selection_records(result), result


def _analysis_result(
    *,
    name: str,
    record_type: str,
    output: Sequence[Mapping[str, object]],
    selection: QueryResult,
    input_count: int,
) -> QueryResult:
    records = records_from_mappings(
        record_type,
        tuple(_scalar_fields(row) for row in output),
    )
    return QueryResult(
        tool=name,
        status="ok",
        records=records,
        diagnostics=(
            DiagnosticV1(
                code="bounded_current_news_analysis",
                message=(
                    "The deterministic analysis used only bounded retained "
                    "headline metadata selected from the local news store."
                ),
                metrics=fields_from_mapping(
                    {
                        "input_record_count": input_count,
                        "output_record_count": len(records),
                    }
                ),
            ),
        ),
        warnings=selection.warnings,
        lineage=selection.lineage,
        truncation=TruncationV1(
            applied=selection.truncation.has_more,
            limit=10_000,
            returned_count=len(records),
            total_known_count=None if selection.truncation.has_more else len(records),
            has_more=selection.truncation.has_more,
        ),
    )


def _source_status(
    arguments: NewsSourceStatusArgumentsV1,
    context: ToolExecutionContext,
    registry: Registry,
) -> QueryResult:
    context.checkpoint()
    rows = CurrentNewsToolRepository(context.store_map, registry).source_status(
        arguments.source_ids
    )
    records = records_from_mappings(
        "current_news_source_status",
        tuple(_scalar_fields(row) for row in rows),
    )
    context.checkpoint()
    return QueryResult(
        tool="news.get_source_status",
        records=records,
        diagnostics=(
            DiagnosticV1(
                code="retained_news_source_status",
                message=(
                    "Status reflects retained attempts and captures; scheduler "
                    "credential-unavailable outcomes are not retained here."
                ),
                metrics=fields_from_mapping({"source_count": len(records)}),
            ),
        ),
        warnings=(
            WarningV1(
                code="scheduler_unavailable_status_not_retained",
                message=(
                    "A missing scheduler credential can stop a source before an "
                    "outcome is written to the news store."
                ),
            ),
        ),
        truncation=TruncationV1(False, 8, len(records), len(records), False),
    )


def _news_lineage(version: Mapping[str, object]) -> tuple[LineageRef, ...]:
    source_id = version.get("source_id")
    capture_id = version.get("capture_id")
    version_id = version.get("article_version_id")
    if not all(isinstance(item, str) and item for item in (source_id, capture_id, version_id)):
        raise ValidationError("Current-news version lineage is invalid")
    if source_id == FMP_STOCK_LATEST_SOURCE_ID:
        evidence_dataset = CURRENT_NEWS_EVIDENCE_DATASET_ID
        article_dataset = CURRENT_NEWS_ARTICLES_DATASET_ID
    else:
        evidence_dataset = CURRENT_MULTI_SOURCE_REPOSITORY_EVIDENCE_DATASET_ID
        article_dataset = CURRENT_MULTI_SOURCE_REPOSITORY_ARTICLES_DATASET_ID
    return (
        LineageRef(
            dataset_id=evidence_dataset,
            store_role="news",
            semantic_id=capture_id,
            evidence_id=capture_id,
            snapshot_id=capture_id,
        ),
        LineageRef(
            dataset_id=article_dataset,
            store_role="news",
            semantic_id=version_id,
            evidence_id=capture_id,
            snapshot_id=capture_id,
            canonical_version_id=version_id,
        ),
    )


def _item_history(
    arguments: NewsItemHistoryArgumentsV1,
    context: ToolExecutionContext,
    registry: Registry,
) -> QueryResult:
    context.checkpoint()
    history = CurrentNewsToolRepository(context.store_map, registry).item_history(
        arguments.article_id,
        arguments.limit,
    )
    if history is None:
        return QueryResult(
            tool="news.get_item_history",
            status="not_established",
            diagnostics=(
                DiagnosticV1(
                    "news_item_not_found",
                    "No retained current-news item matched the exact article identity.",
                ),
            ),
            warnings=(
                WarningV1("not_established", "No version history was fabricated."),
            ),
            truncation=TruncationV1(False, arguments.limit, 0, 0, False),
        )
    versions = history["versions"]
    if not isinstance(versions, tuple):
        raise ValidationError("Current-news item history is invalid")
    records = records_from_mappings(
        "current_news_item_version",
        tuple(_scalar_fields(version) for version in versions),
    )
    lineage = tuple(
        reference for version in versions for reference in _news_lineage(version)
    )
    total = history["total_version_count"]
    if isinstance(total, bool) or not isinstance(total, int):
        raise ValidationError("Current-news history count is invalid")
    truncated = bool(history["truncated"])
    context.checkpoint()
    return QueryResult(
        tool="news.get_item_history",
        records=records,
        diagnostics=(
            DiagnosticV1(
                "retained_news_item_history",
                "Immutable versions and capture memberships were returned in sequence order.",
                fields_from_mapping(
                    {
                        "article_id": arguments.article_id,
                        "returned_version_count": len(records),
                        "total_version_count": total,
                    }
                ),
            ),
        ),
        warnings=(
            WarningV1(
                "capture_absence_is_not_retraction",
                "Absence from a later partial capture is not evidence of retraction.",
            ),
        ),
        lineage=lineage,
        truncation=TruncationV1(
            truncated,
            arguments.limit,
            len(records),
            total,
            truncated,
        ),
    )


def _event_contract(
    arguments: NewsEventImpactArgumentsV1,
    inputs: tuple[TimeSeries, ...],
    lineage: tuple[LineageRef, ...],
):
    return research_envelope(
        "research.news_event_impact",
        {
            "parameters": (
                {"name": "article_version_id", "value": arguments.article_version_id},
                {"name": "instrument_id", "value": arguments.instrument_id},
                {"name": "pre_observations", "value": arguments.pre_observations},
                {"name": "post_observations", "value": arguments.post_observations},
            )
        },
        inputs,
        lineage,
        point_in_time_status="not_established",
        unsafe_reasons=("retrospective_current_data_not_point_in_time_replay",),
    )


def _event_not_established(
    arguments: NewsEventImpactArgumentsV1,
    *,
    reason: str,
    lineage: tuple[LineageRef, ...] = (),
    inputs: tuple[TimeSeries, ...] = (),
) -> QueryResult:
    return QueryResult(
        tool="research.news_event_impact",
        status="not_established",
        diagnostics=(DiagnosticV1(reason, "The requested news-event outcome is not established."),),
        warnings=(WarningV1(reason, "No return outcome was fabricated."),),
        lineage=lineage,
        truncation=TruncationV1(False, 1, 0, 0, False),
        research_contract=_event_contract(arguments, inputs, lineage),
    )


def _event_impact(
    arguments: NewsEventImpactArgumentsV1,
    context: ToolExecutionContext,
    registry: Registry,
) -> QueryResult:
    context.checkpoint()
    version = CurrentNewsToolRepository(context.store_map, registry).article_version(
        arguments.article_version_id
    )
    if version is None:
        return _event_not_established(arguments, reason="news_article_version_not_found")
    news_lineage = _news_lineage(version)
    available_at = version.get("available_at")
    if not isinstance(available_at, str) or len(available_at) < 10:
        raise ValidationError("Current-news availability is invalid")
    try:
        capture_day = date.fromisoformat(available_at[:10])
    except ValueError as exc:
        raise ValidationError("Current-news availability is invalid") from exc
    range_padding = timedelta(days=120)
    close = Stage10DailyPriceRepository(context.store_map, registry).get_close_series(
        Stage10DailyPriceQuery(
            identifier=arguments.instrument_id,
            identifier_kind="instrument_id",
            start_date=(capture_day - range_padding).isoformat(),
            end_date=(capture_day + range_padding).isoformat(),
            mode="latest",
            as_of=None,
            date_only_policy="completed_date",
            limit=500,
        )
    )
    market_lineage = LineageRef(
        dataset_id=STAGE10_PRICE_DATASET_ID,
        store_role="market",
        semantic_id=close.lineage_digest,
    )
    lineage = (*news_lineage, market_lineage)
    provider_symbol = close.metadata.get("provider_symbol")
    symbols = version.get("symbols")
    if (
        not isinstance(provider_symbol, str)
        or not isinstance(symbols, tuple)
        or provider_symbol.casefold()
        not in {item.casefold() for item in symbols if isinstance(item, str)}
    ):
        return _event_not_established(
            arguments,
            reason="news_symbol_does_not_match_instrument",
            lineage=lineage,
            inputs=(close,),
        )
    capture_date = available_at[:10]
    anchor_index = next(
        (
            index
            for index, observation in enumerate(close.observations)
            if observation.period_start > capture_date
        ),
        None,
    )
    if anchor_index is None:
        return _event_not_established(
            arguments,
            reason="no_observed_market_date_after_news_capture",
            lineage=lineage,
            inputs=(close,),
        )
    returns = return_series(
        close,
        direction="trailing",
        method="simple",
        horizon=1,
    )
    outcome = event_study(
        [observation.value for observation in returns.observations],
        event_index=anchor_index,
        pre=arguments.pre_observations,
        post=arguments.post_observations,
    )
    if outcome["status"] != "established":
        reason = outcome.get("reason")
        if not isinstance(reason, str):
            raise ValidationError("News-event outcome reason is invalid")
        return _event_not_established(
            arguments,
            reason=reason,
            lineage=lineage,
            inputs=(close,),
        )
    start = outcome["window_start"]
    end = outcome["window_end"]
    if isinstance(start, bool) or not isinstance(start, int) or isinstance(end, bool) or not isinstance(end, int):
        raise ValidationError("News-event return window is invalid")
    row = {
        "analysis_status": "established_retrospective",
        "article_id": version["article_id"],
        "article_version_id": version["article_version_id"],
        "source_id": version["source_id"],
        "instrument_id": arguments.instrument_id,
        "provider_symbol": provider_symbol,
        "news_available_at": available_at,
        "anchor_rule": "first_observed_trade_date_strictly_after_capture_calendar_date",
        "anchor_trade_date": returns.observations[anchor_index].period_start,
        "pre_observations": arguments.pre_observations,
        "post_observations": arguments.post_observations,
        "window_start_trade_date": returns.observations[start].period_start,
        "window_end_trade_date": returns.observations[end].period_start,
        "sample_size": outcome["sample_size"],
        "cumulative_simple_return": outcome["cumulative_return"],
        "causal_interpretation": False,
        "abnormal_return_status": "not_computed",
        "session_calendar_status": "not_established",
    }
    warnings = (
        WarningV1(
            "retrospective_current_data_not_point_in_time_replay",
            "The output uses current retained news and market data, not a historical replay.",
        ),
        WarningV1(
            "session_calendar_not_established",
            "The anchor is the next observed market date, not an exchange-calendar decision.",
        ),
        WarningV1(
            "not_causal_or_abnormal_return",
            "The cumulative return is descriptive and does not establish causality or abnormal return.",
        ),
    )
    context.checkpoint()
    return QueryResult(
        tool="research.news_event_impact",
        records=records_from_mappings("news_event_impact", (_scalar_fields(row),)),
        diagnostics=(
            DiagnosticV1(
                "retrospective_news_event_impact",
                "A bounded close-to-close return window was anchored after local news capture.",
                fields_from_mapping(
                    {
                        "event_index": anchor_index,
                        "return_method": "simple",
                        "return_horizon": 1,
                    }
                ),
            ),
        ),
        warnings=warnings,
        lineage=lineage,
        truncation=TruncationV1(False, 1, 1, 1, False),
        research_contract=_event_contract(arguments, (close,), lineage),
    )


def invoke_news_research_tool(
    name: str,
    arguments: object,
    context: ToolExecutionContext,
    registry: Registry,
) -> QueryResult:
    """Invoke one fixed read-only news or cross-store research operation."""

    if name not in NEWS_RESEARCH_TOOL_NAMES:
        raise LookupError("News research operation is not registered")
    if not isinstance(registry, Registry):
        raise ValidationError("News research registry is invalid")
    if name == "news.get_source_status" and isinstance(arguments, NewsSourceStatusArgumentsV1):
        return _source_status(arguments, context, registry)
    if name == "news.get_item_history" and isinstance(arguments, NewsItemHistoryArgumentsV1):
        return _item_history(arguments, context, registry)
    if name == "research.news_event_impact" and isinstance(arguments, NewsEventImpactArgumentsV1):
        return _event_impact(arguments, context, registry)
    if not isinstance(arguments, NewsAnalysisArgumentsV1):
        raise ValidationError("News analysis requires typed selection arguments")

    rows, selection = _selected_news(arguments, context, registry)
    if name == "news.story_clusters" and isinstance(arguments, NewsStoryClusterArgumentsV1):
        output = story_clusters(rows, arguments.window_hours)
        record_type = "news_story_candidate_cluster"
    elif name == "news.entity_coverage":
        output = entity_coverage(rows)
        record_type = "news_provider_symbol_coverage"
    elif name == "news.attention_metrics" and isinstance(arguments, NewsAttentionArgumentsV1):
        output = attention_metrics(rows, arguments.bucket, arguments.baseline_periods)
        record_type = "news_attention_bucket"
    elif name == "news.classify_events":
        output = classify_events(rows)
        record_type = "news_event_classification"
    elif name == "news.headline_sentiment":
        output = headline_sentiment(rows)
        record_type = "news_headline_sentiment"
    else:
        raise ValidationError("News analysis arguments do not match the selected tool")
    context.checkpoint()
    return _analysis_result(
        name=name,
        record_type=record_type,
        output=output,
        selection=selection,
        input_count=len(rows),
    )


__all__ = ("NEWS_RESEARCH_TOOL_NAMES", "invoke_news_research_tool")
