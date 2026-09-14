"""Route explicit price-metadata successors through unchanged calculations."""
from dataclasses import replace

from quant_data.market.provider_close_series import ProviderClosePriceRepository
from .price_basis_versions import PRICE_BASIS_VERSIONS


def invoke_price_basis_tool(name, arguments, context, registry):
    base_version, version = PRICE_BASIS_VERSIONS[name]
    declaration = registry.tool(name, version)
    if (context.tool_version != version or
            context.operation_version != declaration["operation_version"] or
            context.operation_graph_id != declaration["operation_graph_id"]):
        raise LookupError("Selected price-metadata operation graph is invalid")
    base = registry.tool(name, base_version)
    calculation_context = replace(
        context, tool_version=base_version,
        operation_version=base["operation_version"],
        operation_graph_id=base["operation_graph_id"],
    )
    if name == "market.get_price_series":
        from .market_prices import invoke_stage10_market_price
        return invoke_stage10_market_price(
            name, arguments, calculation_context, registry,
            repository_type=ProviderClosePriceRepository,
        )
    if name in {"market.get_returns", "market.get_forward_returns"}:
        from .market_returns import invoke_stage10_market_return
        return invoke_stage10_market_return(
            name, arguments, calculation_context, registry,
            repository_type=ProviderClosePriceRepository,
        )
    if name == "market.cross_sectional_performance":
        from .market_cross_sectional import invoke_stage10_cross_sectional_analytics_v21
        return invoke_stage10_cross_sectional_analytics_v21(
            name, arguments, calculation_context, registry,
            repository_type=ProviderClosePriceRepository,
        )
    if name == "research.news_event_impact":
        from .news_research import invoke_news_research_tool
        return invoke_news_research_tool(
            name, arguments, calculation_context, registry,
            repository_type=ProviderClosePriceRepository,
        )
    from .operations import invoke_operation
    return invoke_operation(name, arguments, calculation_context, registry)
