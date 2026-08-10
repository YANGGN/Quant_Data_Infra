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

from .catalog import LEGACY_TOOL_NAMES, PUBLIC_TOOL_NAMES
from .context import ToolExecutionContext
from .results import (
    DiagnosticV1,
    QueryResult,
    ResearchEnvelopeV1,
    fields_from_mapping,
    research_envelope,
)


_RESEARCH_PREFIXES = ("research.", "data.", "alpha.", "stats.", "forecast.")
REGISTERED_STAGE5_OPERATIONS = frozenset(PUBLIC_TOOL_NAMES) - frozenset(
    LEGACY_TOOL_NAMES
)


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

    if name not in REGISTERED_STAGE5_OPERATIONS:
        raise LookupError("Operation graph is not registered")
    context.checkpoint()
    context.budget.require(
        rows=_limit(arguments),
        series=_series_count(arguments),
        operations=min(_limit(arguments) * max(_series_count(arguments), 1), 5_000_000),
    )
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
