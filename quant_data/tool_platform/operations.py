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
    ADDITIVE_PUBLIC_TOOL_NAMES,
    ADDITIVE_STAGE10_STATISTICS_TOOLS,
    CURRENT_PUBLIC_TOOL_NAMES,
    LEGACY_TOOL_NAMES,
    PUBLIC_TOOL_NAMES,
    VERSIONED_CANONICAL_MACRO_TOOLS,
    VERSIONED_DATA_QUALITY_TOOLS,
    VERSIONED_ECONOMETRICS_TOOLS,
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
        context.tool_version == "2.0.0"
        and name in VERSIONED_TECHNICAL_INDICATOR_TOOLS
    )
    if name not in REGISTERED_STAGE5_OPERATIONS and not (
        versioned_macro
        or versioned_quality
        or versioned_timeseries
        or versioned_econometrics
        or versioned_technical_indicator
    ):
        raise LookupError("Operation graph is not registered")
    context.checkpoint()
    context.budget.require(
        rows=_limit(arguments),
        series=_series_count(arguments),
        operations=min(_limit(arguments) * max(_series_count(arguments), 1), 5_000_000),
    )
    if versioned_macro:
        expected_graph = f"tool_platform.{name}.v2"
        if context.operation_graph_id != expected_graph:
            raise LookupError("Selected canonical macro operation graph is invalid")
        from .macro_access import invoke_canonical_macro

        return invoke_canonical_macro(name, arguments, context, registry)
    if name in ADDITIVE_PUBLIC_TOOL_NAMES:
        expected_graph = f"tool_platform.{name}.v1"
        if (
            context.tool_version != "1.0.0"
            or context.operation_graph_id != expected_graph
        ):
            raise LookupError("Selected additive operation graph is invalid")
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
        expected_graph = "tool_platform.market.technical_indicators.v2"
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
