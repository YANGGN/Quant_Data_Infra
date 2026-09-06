"""Closed Stage 5 operation-graph resolver.

Public names resolve through this static module; caller text is never treated
as an import path.  Domain and analytical lanes expose one narrow function
each, while semantically unavailable fixture capabilities return an explicit
``not_established`` result instead of fabricated data.
"""

from __future__ import annotations

from typing import Any, Mapping

from quant_data.contracts import TruncationV1, WarningV1
from quant_data.registry import Registry

from .catalog import (
    ADDITIVE_DATA_STATUS_TOOLS,
    ADDITIVE_NEWS_RESEARCH_TOOLS,
    ADDITIVE_PUBLIC_TOOL_NAMES,
    ADDITIVE_STAGE10_STATISTICS_TOOLS,
    CURRENT_PUBLIC_TOOL_NAMES,
    LEGACY_TOOL_NAMES,
    PUBLIC_TOOL_NAMES,
    VERSIONED_MARKET_INSTRUMENT_SEARCH_TOOLS,
    VERSIONED_NEWS_TOOLS,
    VERSIONED_OPTIONS_ACCESS_TOOLS,
    VERSIONED_CANONICAL_MACRO_TOOLS,
    VERSIONED_COMPANY_FILING_TOOLS,
    VERSIONED_COMPANY_SHARE_COUNT_TOOLS,
    VERSIONED_DATA_QUALITY_TOOLS,
    VERSIONED_ECONOMETRICS_TOOLS,
    VERSIONED_ENERGY_TOOLS,
    VERSIONED_COMPANY_FUNDAMENTAL_TOOLS,
    VERSIONED_MACRO_CONDITION_TOOLS,
    VERSIONED_RATE_TOOLS,
    VERSIONED_RESEARCH_ANALYTIC_TOOLS,
    VERSIONED_RESEARCH_STATE_TOOLS,
    VERSIONED_TECHNICAL_INDICATOR_TOOLS,
    VERSIONED_TIMESERIES_ANALYSIS_TOOLS,
)
from .context import ToolExecutionContext
from .results import (
    DiagnosticV1,
    QueryResult,
    ResearchEnvelopeV1,
    fields_from_mapping,
    research_envelope,
)


_RESEARCH_PREFIXES = ("research.", "data.", "alpha.", "stats.", "forecast.")
REGISTERED_STAGE5_OPERATIONS = frozenset(
    CURRENT_PUBLIC_TOOL_NAMES
) - frozenset(LEGACY_TOOL_NAMES)


def _limit(arguments: Mapping[str, Any]) -> int:
    value = arguments.get("limit", 10_000)
    return value if isinstance(value, int) and not isinstance(value, bool) else 10_000


def _series_count(arguments: Mapping[str, Any]) -> int:
    value = arguments.get("series")
    if isinstance(value, (list, tuple)):
        return len(value)
    return 1 if value is not None else 0


def _not_established(name: str, arguments: Mapping[str, Any]) -> QueryResult:
    research = name.startswith(_RESEARCH_PREFIXES)
    return QueryResult(
        tool=name,
        status="not_established",
        diagnostics=(
            DiagnosticV1(
                "fixture_semantics_not_established",
                "The offline fixtures do not establish this analytical result.",
                fields_from_mapping(
                    {
                        "forward_reconstructed_contract": True,
                        "input_series_count": _series_count(arguments),
                    }
                ),
            ),
        ),
        warnings=(
            WarningV1(
                "not_established",
                "No historical or live value was fabricated for this request.",
            ),
        ),
        truncation=TruncationV1(False, _limit(arguments), 0, 0, False),
        research_contract=research_envelope(
            name, arguments, (), (),
        ) if research else ResearchEnvelopeV1(),
    )


def invoke_operation(
    name: str,
    arguments: Mapping[str, Any],
    context: ToolExecutionContext,
    registry: Registry,
) -> QueryResult:
    """Invoke one closed read-only graph and return an immutable typed result."""

    versioned_timeseries = (
        context.tool_version == "2.0.0"
        and name in VERSIONED_TIMESERIES_ANALYSIS_TOOLS
    )
    versioned_macro = (
        context.tool_version == "2.0.0"
        and name in VERSIONED_CANONICAL_MACRO_TOOLS
    )
    versioned_quality = (
        context.tool_version == "2.0.0"
        and name in VERSIONED_DATA_QUALITY_TOOLS
    )
    versioned_econometrics = (
        (
            context.tool_version == "2.0.0"
            and name in VERSIONED_ECONOMETRICS_TOOLS
        )
        or (
            context.tool_version == "2.1.0"
            and name in {
                "econometrics.regression",
                "econometrics.rolling_regression",
                "econometrics.stationarity",
            }
        )
        or (
            context.tool_version == "3.0.0"
            and name == "econometrics.regression"
        )
    )
    versioned_technical_indicator = (
        context.tool_version in {"2.0.0", "2.1.0", "2.2.0", "2.3.0", "2.4.0", "2.5.0", "2.6.0", "2.7.0"}
        and name in VERSIONED_TECHNICAL_INDICATOR_TOOLS
    )
    versioned_research_analytic = (
        context.tool_version == "2.0.0"
        and name in VERSIONED_RESEARCH_ANALYTIC_TOOLS
    )
    versioned_company_filing = (
        context.tool_version == "2.0.0"
        and name in VERSIONED_COMPANY_FILING_TOOLS
    )
    versioned_company_share_count = (
        context.tool_version == "2.0.0"
        and name in VERSIONED_COMPANY_SHARE_COUNT_TOOLS
    )
    versioned_market_instrument_search = (
        context.tool_version == "2.0.0"
        and name in VERSIONED_MARKET_INSTRUMENT_SEARCH_TOOLS
    )
    versioned_news = (
        context.tool_version in {"2.0.0", "2.1.0", "2.2.0"}
        and name in VERSIONED_NEWS_TOOLS
    )
    versioned_options_access = (
        context.tool_version == "2.0.0"
        and name in VERSIONED_OPTIONS_ACCESS_TOOLS
    )
    if name not in REGISTERED_STAGE5_OPERATIONS and not (
        versioned_macro
        or versioned_quality
        or versioned_timeseries
        or versioned_econometrics
        or versioned_technical_indicator
        or versioned_research_analytic
        or versioned_company_filing
        or versioned_company_share_count
        or versioned_market_instrument_search
        or versioned_news
        or versioned_options_access
    ):
        raise LookupError("Operation graph is not registered")
    context.checkpoint()
    requested_rows = 1 if name == "price_realtime" else _limit(arguments)
    context.budget.require(
        rows=requested_rows,
        series=_series_count(arguments),
        operations=min(requested_rows * max(_series_count(arguments), 1), 5_000_000),
    )
    if versioned_macro:
        expected_graph = f"tool_platform.{name}.v2"
        if context.operation_graph_id != expected_graph:
            raise LookupError("Selected canonical macro operation graph is invalid")
        from .macro_access import invoke_canonical_macro

        return invoke_canonical_macro(name, arguments, context, registry)
    if versioned_company_filing:
        expected_graph = "tool_platform.company.search_filings.v2"
        if context.operation_graph_id != expected_graph:
            raise LookupError("Selected company filing operation graph is invalid")
        from .domain_operations import invoke_domain_operation

        return invoke_domain_operation(name, arguments, context, registry)
    if versioned_company_share_count:
        expected_graph = "tool_platform.company.get_share_count_history.v2"
        if context.operation_graph_id != expected_graph:
            raise LookupError(
                "Selected company share-count operation graph is invalid"
            )
        from .company_access import invoke_company_share_count_history

        return invoke_company_share_count_history(
            name, arguments, context, registry
        )
    if versioned_market_instrument_search:
        expected_graph = "tool_platform.market.search_instruments.v2"
        if context.operation_graph_id != expected_graph:
            raise LookupError(
                "Selected market instrument-search operation graph is invalid"
            )
        from .market_tickers import invoke_stage10_market_instrument_search

        return invoke_stage10_market_instrument_search(
            name, arguments, context, registry
        )
    if versioned_news:
        expected_graph = {
            "2.0.0": "tool_platform.news.search.v2",
            "2.1.0": "tool_platform.news.search.v2_1",
            "2.2.0": "tool_platform.news.search.v2_2",
        }[context.tool_version]
        if context.operation_graph_id != expected_graph:
            raise LookupError("Selected current-news operation graph is invalid")
        from .news_access import (
            invoke_news_search_v2,
            invoke_news_search_v21,
            invoke_news_search_v22,
        )

        if context.tool_version == "2.2.0":
            return invoke_news_search_v22(
                name, arguments, context, registry
            )
        if context.tool_version == "2.1.0":
            return invoke_news_search_v21(
                name, arguments, context, registry
            )
        return invoke_news_search_v2(name, arguments, context, registry)
    if versioned_options_access:
        expected_graph = f"tool_platform.{name}.v2"
        if context.operation_graph_id != expected_graph:
            raise LookupError("Selected options-v2 operation graph is invalid")
        from .options_access import invoke_options_v2

        return invoke_options_v2(name, arguments, context, registry)
    if name in ADDITIVE_NEWS_RESEARCH_TOOLS:
        expected_graph = f"tool_platform.{name}.v1"
        if context.tool_version != "1.0.0" or context.operation_graph_id != expected_graph:
            raise LookupError("Selected news-research operation graph is invalid")
        from .news_research import invoke_news_research_tool

        return invoke_news_research_tool(name, arguments, context, registry)
    if context.tool_version == "2.0.0" and name == "macro.get_release_calendar":
        expected_graph = "tool_platform.macro.get_release_calendar.v2"
        if context.operation_graph_id != expected_graph:
            raise LookupError(
                "Selected release-calendar v2 operation graph is invalid"
            )
        from .macro_access import invoke_release_calendar_v2

        return invoke_release_calendar_v2(
            name, arguments, context, registry
        )
    if (
        context.tool_version == "2.0.0"
        and name == "market.cross_sectional_performance"
    ):
        expected_graph = "tool_platform.market.cross_sectional_performance.v2"
        if context.operation_graph_id != expected_graph:
            raise LookupError(
                "Selected cross-sectional operation graph is invalid"
            )
        from .market_cross_sectional import (
            invoke_stage10_cross_sectional_performance,
        )

        return invoke_stage10_cross_sectional_performance(
            name, arguments, context, registry
        )
    if (
        context.tool_version == "2.1.0"
        and name == "market.cross_sectional_performance"
    ):
        expected_graph = "tool_platform.market.cross_sectional_performance.v2_1"
        if context.operation_graph_id != expected_graph:
            raise LookupError(
                "Selected cross-sectional analytics graph is invalid"
            )
        from .market_cross_sectional import (
            invoke_stage10_cross_sectional_analytics_v21,
        )

        return invoke_stage10_cross_sectional_analytics_v21(
            name, arguments, context, registry
        )
    if context.tool_version == "2.0.0" and name in (
        *VERSIONED_MACRO_CONDITION_TOOLS,
        *VERSIONED_RATE_TOOLS,
        *VERSIONED_RESEARCH_STATE_TOOLS,
    ):
        expected_graph = f"tool_platform.{name}.v2"
        if context.operation_graph_id != expected_graph:
            raise LookupError("Selected macro-condition operation graph is invalid")
        from .macro_conditions import invoke_macro_conditions

        return invoke_macro_conditions(name, arguments, context, registry)
    if context.tool_version == "2.1.0" and name in VERSIONED_ENERGY_TOOLS:
        expected_graph = f"tool_platform.{name}.v2_1"
        if context.operation_graph_id != expected_graph:
            raise LookupError("Selected energy analytics graph is invalid")
        from .energy_access import invoke_energy_access_v21

        return invoke_energy_access_v21(name, arguments, context, registry)
    if context.tool_version == "2.0.0" and name in VERSIONED_ENERGY_TOOLS:
        expected_graph = f"tool_platform.{name}.v2"
        if context.operation_graph_id != expected_graph:
            raise LookupError("Selected energy operation graph is invalid")
        from .energy_access import invoke_energy_access

        return invoke_energy_access(name, arguments, context, registry)
    if (
        context.tool_version == "2.0.0"
        and name in VERSIONED_COMPANY_FUNDAMENTAL_TOOLS
    ):
        expected_graph = "tool_platform.company.get_fundamentals.v2"
        if context.operation_graph_id != expected_graph:
            raise LookupError("Selected company-fundamentals graph is invalid")
        from .company_fundamentals import invoke_company_fundamentals

        return invoke_company_fundamentals(name, arguments, context, registry)
    if (
        context.tool_version == "2.1.0"
        and name in VERSIONED_COMPANY_FUNDAMENTAL_TOOLS
    ):
        expected_graph = "tool_platform.company.get_fundamentals.v2_1"
        if context.operation_graph_id != expected_graph:
            raise LookupError("Selected company-ratios graph is invalid")
        from .company_fundamentals import invoke_company_fundamentals_v21

        return invoke_company_fundamentals_v21(
            name, arguments, context, registry
        )
    if name in ADDITIVE_PUBLIC_TOOL_NAMES:
        expected_graph = f"tool_platform.{name}.v1"
        if (
            context.tool_version != "1.0.0"
            or context.operation_graph_id != expected_graph
        ):
            raise LookupError("Selected additive operation graph is invalid")
        if name == "price_realtime":
            from .realtime_quote import invoke_quote
            return invoke_quote(arguments, context)
        if name == "company.get_research_inputs":
            from quant_data.company.fmp_research import read_research_inputs
            return read_research_inputs(context, arguments)
        if name == "portfolio.get_etf_allocator_snapshot":
            from .etf_snapshot import invoke_etf_snapshot
            return invoke_etf_snapshot(name, arguments, context, registry)
        if name in ADDITIVE_DATA_STATUS_TOOLS:
            from .data_status_access import invoke_dataset_status

            return invoke_dataset_status(name, arguments, context, registry)
        if name in ADDITIVE_STAGE10_STATISTICS_TOOLS:
            from .market_analysis_adapter import (
                invoke_stage10_analysis_foundation,
            )

            return invoke_stage10_analysis_foundation(
                name, arguments, context
            )
        if name == "market.get_available_ticker":
            from .market_tickers import invoke_stage10_available_tickers

            return invoke_stage10_available_tickers(
                name, arguments, context, registry
            )
        if name == "market.get_price_series":
            from .market_prices import invoke_stage10_market_price

            return invoke_stage10_market_price(
                name, arguments, context, registry
            )
        if name == "market.get_volume_series":
            from .market_volume import invoke_stage10_market_volume

            return invoke_stage10_market_volume(
                name, arguments, context, registry
            )
        if name == "macro.get_release_calendar":
            from .macro_access import invoke_release_calendar

            return invoke_release_calendar(name, arguments, context, registry)
        raise LookupError("Selected additive operation is invalid")
    if context.tool_version == "2.0.0" and name in {
        "market.get_returns",
        "market.get_forward_returns",
    }:
        expected_graph = f"tool_platform.{name}.v2"
        if context.operation_graph_id != expected_graph:
            raise LookupError("Selected market-return operation graph is invalid")
        from .market_returns import invoke_stage10_market_return

        return invoke_stage10_market_return(name, arguments, context, registry)
    if versioned_technical_indicator:
        expected_graph = (
            {
                "2.0.0": "tool_platform.market.technical_indicators.v2",
                "2.1.0": "tool_platform.market.technical_indicators.v2_1",
                "2.2.0": "tool_platform.market.technical_indicators.v2_2",
                "2.3.0": "tool_platform.market.technical_indicators.v2_3",
                "2.4.0": "tool_platform.market.technical_indicators.v2_4",
                "2.5.0": "tool_platform.market.technical_indicators.v2_5",
                "2.6.0": "tool_platform.market.technical_indicators.v2_6",
                "2.7.0": "tool_platform.market.technical_indicators.v2_7",
            }[context.tool_version]
        )
        if context.operation_graph_id != expected_graph:
            raise LookupError(
                "Selected technical-indicator operation graph is invalid"
            )
        from .technical_indicator_adapter import (
            invoke_stage10_technical_indicator,
        )

        return invoke_stage10_technical_indicator(name, arguments, context)
    if versioned_timeseries:
        expected_graph = f"tool_platform.{name}.v2"
        if context.operation_graph_id != expected_graph:
            raise LookupError("Selected Stage 10 statistic operation graph is invalid")
        if name == "timeseries.transform":
            from .market_analysis_adapter import (
                invoke_stage10_analysis_foundation,
            )

            return invoke_stage10_analysis_foundation(
                name, arguments, context
            )
        from .market_statistics import invoke_stage10_market_statistic

        return invoke_stage10_market_statistic(name, arguments, context)
    if versioned_quality:
        expected_graph = f"tool_platform.{name}.v2"
        if context.operation_graph_id != expected_graph:
            raise LookupError("Selected data-quality operation graph is invalid")
        from .market_analysis_adapter import invoke_stage10_analysis_foundation

        return invoke_stage10_analysis_foundation(name, arguments, context)
    if versioned_econometrics:
        graph_suffix = {
            "2.0.0": "v2",
            "2.1.0": "v2_1",
            "3.0.0": "v3",
        }[context.tool_version]
        expected_graph = f"tool_platform.{name}.{graph_suffix}"
        if context.operation_graph_id != expected_graph:
            raise LookupError("Selected Stage 10 econometrics graph is invalid")
        from .market_econometrics import invoke_stage10_market_econometric

        return invoke_stage10_market_econometric(name, arguments, context)
    if name == "macro.get_intraday_releases":
        context.capabilities.require("macro_intraday_live")

    if versioned_research_analytic and context.operation_graph_id != (
        f"tool_platform.{name}.v2"
    ):
        raise LookupError("Selected Stage 10 research analytic graph is invalid")

    try:
        from .analytic_adapter import ANALYTIC_OPERATION_NAMES, invoke_analytic
    except ImportError:
        ANALYTIC_OPERATION_NAMES = frozenset()
        invoke_analytic = None
    if name in ANALYTIC_OPERATION_NAMES and invoke_analytic is not None:
        result = invoke_analytic(name, arguments)
        if not isinstance(result, QueryResult):
            raise TypeError("Analytical operation returned an invalid typed result")
        context.checkpoint()
        return result

    try:
        from .domain_operations import DOMAIN_OPERATION_NAMES, invoke_domain_operation
    except ImportError:
        DOMAIN_OPERATION_NAMES = frozenset()
        invoke_domain_operation = None
    if name in DOMAIN_OPERATION_NAMES and invoke_domain_operation is not None:
        result = invoke_domain_operation(name, arguments, context, registry)
        if not isinstance(result, QueryResult):
            raise TypeError("Domain operation returned an invalid typed result")
        context.checkpoint()
        return result

    return _not_established(name, arguments)


__all__ = ("REGISTERED_STAGE5_OPERATIONS", "invoke_operation")
