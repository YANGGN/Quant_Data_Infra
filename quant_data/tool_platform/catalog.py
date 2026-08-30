"""Authoritative Stage 5 public-name, schema, and operation metadata.

The recovered evidence preserved the public names but not byte-exact request
and response schemas for 55 of them.  Those entries are deliberately labelled
``forward_reconstructed_v1``.  The two Stage 1 schemas are carried forward
verbatim through ``build_tool_entries`` so historical milestone projections
remain byte-compatible.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping

from .arguments import input_schema as typed_input_schema
from .results import query_result_schema


SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
CATALOG_ID = "quant_data.tool_contract_catalog"
CATALOG_VERSION = "1.0.0"
VERSIONED_CATALOG_ID = "quant_data.tool_contract_catalog.v2"
VERSIONED_CATALOG_VERSION = "2.24.0"

ADDITIVE_STAGE10_STATISTICS_TOOLS = (
    "stats.distribution_diagnostics",
    "stats.covariance_matrix",
    "stats.bootstrap_confidence_interval",
    "stats.principal_components",
)
ADDITIVE_PUBLIC_TOOL_NAMES = (
    "macro.get_release_calendar",
    "market.get_available_ticker",
    "market.get_price_series",
    "market.get_volume_series",
    *ADDITIVE_STAGE10_STATISTICS_TOOLS,
)

VERSIONED_CANONICAL_MACRO_TOOLS = (
    "macro.search_series",
    "macro.describe_series",
    "macro.get_series",
)
VERSIONED_MACRO_CALENDAR_TOOLS = ("macro.get_release_calendar",)
VERSIONED_MARKET_CROSS_SECTIONAL_TOOLS = (
    "market.cross_sectional_performance",
)
VERSIONED_MACRO_CONDITION_TOOLS = (
    "macro.revision_analysis",
    "macro.standardize_surprises",
    "macro.get_liquidity_snapshot",
    "macro.get_liquidity_impulse",
    "macro.get_credit_conditions",
    "macro.regime_snapshot",
)
VERSIONED_RATE_TOOLS = (
    "rates.get_funding_conditions",
    "rates.get_repo_facility_usage",
    "rates.curve_analytics",
)
VERSIONED_ENERGY_TOOLS = (
    "energy.get_electricity_retail_sales",
    "energy.get_weekly_fundamentals",
)
VERSIONED_COMPANY_FUNDAMENTAL_TOOLS = ("company.get_fundamentals",)
VERSIONED_RESEARCH_STATE_TOOLS = ("research.liquidity_credit_state",)
VERSIONED_INVESTMENT_ANALYSIS_TOOLS = (
    *VERSIONED_MACRO_CALENDAR_TOOLS,
    *VERSIONED_MARKET_CROSS_SECTIONAL_TOOLS,
    *VERSIONED_MACRO_CONDITION_TOOLS,
    *VERSIONED_RATE_TOOLS,
    *VERSIONED_ENERGY_TOOLS,
    *VERSIONED_COMPANY_FUNDAMENTAL_TOOLS,
    *VERSIONED_RESEARCH_STATE_TOOLS,
)
VERSIONED_COMPANY_FILING_TOOLS = ("company.search_filings",)
VERSIONED_COMPANY_SHARE_COUNT_TOOLS = ("company.get_share_count_history",)
VERSIONED_MARKET_INSTRUMENT_SEARCH_TOOLS = ("market.search_instruments",)
VERSIONED_NEWS_TOOLS = ("news.search",)
VERSIONED_MARKET_RETURN_TOOLS = (
    "market.get_returns",
    "market.get_forward_returns",
)
VERSIONED_TECHNICAL_INDICATOR_TOOLS = ("market.technical_indicators",)
VERSIONED_TIMESERIES_ANALYSIS_TOOLS = (
    "timeseries.transform",
    "timeseries.describe",
    "timeseries.align",
    "timeseries.correlation",
)
VERSIONED_DATA_QUALITY_TOOLS = ("data.quality_audit",)
VERSIONED_RESEARCH_ANALYTIC_TOOLS = (
    "research.point_in_time_panel",
    "research.event_study",
    "alpha.signal_diagnostics",
    "research.walk_forward_backtest",
    "research.robustness_suite",
    "stats.multiple_testing",
    "forecast.evaluate",
)
VERSIONED_ECONOMETRICS_TOOLS = (
    "econometrics.regression",
    "econometrics.rolling_regression",
    "econometrics.stationarity",
    "econometrics.structural_breaks",
)
VERSIONED_TOOL_NAMES = (
    *VERSIONED_CANONICAL_MACRO_TOOLS,
    *VERSIONED_MARKET_RETURN_TOOLS,
    *VERSIONED_TECHNICAL_INDICATOR_TOOLS,
    *(
        item
        for item in VERSIONED_TIMESERIES_ANALYSIS_TOOLS
        if item != "timeseries.transform"
    ),
    *VERSIONED_ECONOMETRICS_TOOLS,
    *VERSIONED_DATA_QUALITY_TOOLS,
    "timeseries.transform",
    *VERSIONED_RESEARCH_ANALYTIC_TOOLS,
    *VERSIONED_COMPANY_FILING_TOOLS,
    *VERSIONED_COMPANY_SHARE_COUNT_TOOLS,
    *VERSIONED_MARKET_INSTRUMENT_SEARCH_TOOLS,
    *VERSIONED_NEWS_TOOLS,
    *VERSIONED_INVESTMENT_ANALYSIS_TOOLS,
)

PUBLIC_TOOL_NAMES = (
    "macro.search_series",
    "macro.describe_series",
    "macro.get_series",
    "macro.get_intraday_releases",
    "macro.release_surprises",
    "macro.revision_analysis",
    "macro.align_us_recessions",
    "macro.standardize_surprises",
    "macro.get_liquidity_snapshot",
    "macro.get_liquidity_impulse",
    "macro.get_credit_conditions",
    "macro.regime_snapshot",
    "timeseries.transform",
    "timeseries.describe",
    "timeseries.align",
    "timeseries.correlation",
    "econometrics.regression",
    "econometrics.stationarity",
    "econometrics.rolling_regression",
    "econometrics.structural_breaks",
    "econometrics.local_projection",
    "company.search_issuers",
    "company.search_filings",
    "company.get_fundamentals",
    "company.get_corporate_actions",
    "company.get_share_count_history",
    "company.get_earnings_calendar",
    "company.get_consensus_history",
    "company.get_guidance_history",
    "company.get_estimate_revisions",
    "company.get_earnings_setup",
    "energy.get_electricity_retail_sales",
    "energy.get_weekly_fundamentals",
    "market.search_instruments",
    "market.get_returns",
    "market.get_forward_returns",
    "market.technical_indicators",
    "market.cross_sectional_performance",
    "rates.get_funding_conditions",
    "rates.get_repo_facility_usage",
    "rates.curve_analytics",
    "options.search_captures",
    "options.search_contracts",
    "options.get_surface_snapshot",
    "options.surface_diagnostics",
    "options.screen_contracts",
    "options.strategy_scenario",
    "research.point_in_time_panel",
    "data.quality_audit",
    "research.event_study",
    "alpha.signal_diagnostics",
    "research.walk_forward_backtest",
    "research.robustness_suite",
    "stats.multiple_testing",
    "forecast.evaluate",
    "news.search",
    "research.liquidity_credit_state",
)

LEGACY_TOOL_NAMES = ("macro.get_series", "timeseries.describe")

FAMILY_COUNTS = {
    "macro": 12,
    "timeseries": 4,
    "econometrics": 5,
    "company": 10,
    "energy": 2,
    "market": 5,
    "rates": 3,
    "options": 6,
    "research": 10,
}
CURRENT_FAMILY_COUNTS = {
    **FAMILY_COUNTS,
    "macro": 13,
    "market": 8,
    "research": 14,
}
_CURRENT_PUBLIC_NAMES = list(PUBLIC_TOOL_NAMES)
_CURRENT_PUBLIC_NAMES.insert(
    _CURRENT_PUBLIC_NAMES.index("macro.get_series") + 1,
    "macro.get_release_calendar",
)
_CURRENT_MARKET_INSERTION = _CURRENT_PUBLIC_NAMES.index("market.get_returns")
_CURRENT_PUBLIC_NAMES[_CURRENT_MARKET_INSERTION:_CURRENT_MARKET_INSERTION] = [
    "market.get_available_ticker",
    "market.get_price_series",
    "market.get_volume_series",
]
_CURRENT_STATS_INSERTION = _CURRENT_PUBLIC_NAMES.index("stats.multiple_testing")
_CURRENT_PUBLIC_NAMES[
    _CURRENT_STATS_INSERTION:_CURRENT_STATS_INSERTION
] = [
    "stats.distribution_diagnostics",
    "stats.covariance_matrix",
    "stats.bootstrap_confidence_interval",
    "stats.principal_components",
]
CURRENT_PUBLIC_TOOL_NAMES = tuple(_CURRENT_PUBLIC_NAMES)

_SEARCH_TOOLS = frozenset(
    {
        "macro.search_series",
        "company.search_issuers",
        "company.search_filings",
        "market.search_instruments",
        "options.search_captures",
        "options.search_contracts",
        "news.search",
    }
)
_SINGLE_SERIES_TOOLS = frozenset({"timeseries.transform"})
_MULTI_SERIES_TOOLS = frozenset(
    {
        "timeseries.align",
        "timeseries.correlation",
        "econometrics.regression",
        "econometrics.stationarity",
        "econometrics.rolling_regression",
        "econometrics.structural_breaks",
        "econometrics.local_projection",
    }
)
_RESEARCH_TOOLS = frozenset(
    {
        "research.point_in_time_panel",
        "data.quality_audit",
        "research.event_study",
        "alpha.signal_diagnostics",
        "research.walk_forward_backtest",
        "research.robustness_suite",
        "stats.multiple_testing",
        "forecast.evaluate",
        "research.liquidity_credit_state",
    }
)


@dataclass(frozen=True, slots=True)
class ToolProfile:
    name: str
    family: str
    input_kind: str
    stores: tuple[str, ...]
    datasets: tuple[str, ...]
    live_capability: str | None = None

    @property
    def operation_graph_id(self) -> str:
        suffix = ".v1" if self.name in ADDITIVE_PUBLIC_TOOL_NAMES else ""
        return f"tool_platform.{self.name}{suffix}"


_DATASETS: dict[str, tuple[str, ...]] = {
    "macro.search_series": ("fixture.macro.stage3_catalog",),
    "macro.describe_series": ("fixture.macro.stage3_catalog",),
    "macro.get_series": ("fixture.macro.rtdsm_employ",),
    "macro.get_intraday_releases": ("fixture.macro.economic_calendar",),
    "macro.release_surprises": (
        "fixture.macro.economic_calendar",
        "macro.official_vintages",
    ),
    "macro.revision_analysis": (
        "fixture.macro.gdp_vintages",
        "fixture.macro.rtdsm_employ",
    ),
    "macro.align_us_recessions": ("fixture.macro.recession_periods",),
    "macro.standardize_surprises": ("fixture.macro.economic_calendar",),
    "macro.get_liquidity_snapshot": (
        "fixture.macro.soma_summary",
        "fixture.macro.treasury_yield_curves",
    ),
    "macro.get_liquidity_impulse": ("fixture.macro.soma_summary",),
    "macro.get_credit_conditions": ("fixture.macro.stage3_catalog",),
    "macro.regime_snapshot": (
        "fixture.macro.recession_periods",
        "fixture.macro.treasury_yield_curves",
    ),
    "company.search_issuers": ("fixture.company.issuers",),
    "company.search_filings": ("fixture.company.filings",),
    "company.get_fundamentals": ("fixture.company.fundamentals",),
    "company.get_corporate_actions": ("fixture.company.corporate_actions",),
    "company.get_share_count_history": ("fixture.company.fundamentals",),
    "company.get_earnings_calendar": ("fixture.company.expectations",),
    "company.get_consensus_history": ("fixture.company.expectations",),
    "company.get_guidance_history": ("fixture.company.expectations",),
    "company.get_estimate_revisions": ("fixture.company.expectations",),
    "company.get_earnings_setup": (
        "fixture.company.expectations",
        "fixture.company.fundamentals",
    ),
    "energy.get_electricity_retail_sales": ("fixture.macro.eia_retail",),
    "energy.get_weekly_fundamentals": ("fixture.macro.eia_weekly",),
    "market.search_instruments": (
        "fixture.market.instruments",
        "fixture.market.instrument_classifications",
    ),
    "market.get_returns": ("fixture.market.daily_prices",),
    "market.get_forward_returns": ("fixture.market.daily_prices",),
    "market.technical_indicators": ("fixture.market.daily_prices",),
    "market.cross_sectional_performance": (
        "fixture.market.daily_prices",
        "fixture.market.controlled_universes",
    ),
    "rates.get_funding_conditions": ("fixture.macro.treasury_yield_curves",),
    "rates.get_repo_facility_usage": ("fixture.macro.soma_summary",),
    "rates.curve_analytics": ("fixture.macro.treasury_yield_curves",),
    "options.search_captures": ("fixture.market.option_capture_evidence",),
    "options.search_contracts": ("fixture.market.options",),
    "options.get_surface_snapshot": ("fixture.market.options",),
    "options.surface_diagnostics": ("fixture.market.options",),
    "options.screen_contracts": ("fixture.market.options",),
    "options.strategy_scenario": ("fixture.market.options",),
    "news.search": ("fixture.news.items", "fixture.news.search_index"),
    "research.liquidity_credit_state": (
        "fixture.macro.soma_summary",
        "fixture.macro.treasury_yield_curves",
    ),
}


def current_tool_profiles() -> tuple[ToolProfile, ...]:
    """Return the recovered inventory plus reviewed additive native tools."""

    additive = (
        ToolProfile(
            name="macro.get_release_calendar",
            family="macro",
            input_kind="macro_release_calendar_v1",
            stores=("macro",),
            datasets=(
                "macro.fmp.economic_calendar_evidence",
                "macro.fmp.economic_calendar_incremental_evidence",
                "macro.fmp.economic_calendar_incremental_events",
            ),
        ),
        ToolProfile(
            name="market.get_available_ticker",
            family="market",
            input_kind="stage10_available_ticker_v1",
            stores=("market",),
            datasets=(
                "market.stage10.instruments",
                "market.stage10.daily_prices",
                "market.stage10.source_evidence",
            ),
        ),
        ToolProfile(
            name="market.get_price_series",
            family="market",
            input_kind="stage10_market_price_v1",
            stores=("market",),
            datasets=(
                "market.stage10.daily_prices",
                "market.stage10.source_evidence",
                "market.stage10.instruments",
            ),
        ),
        ToolProfile(
            name="market.get_volume_series",
            family="market",
            input_kind="stage10_market_volume_v1",
            stores=("market",),
            datasets=(
                "market.stage10.daily_prices",
                "market.stage10.source_evidence",
                "market.stage10.instruments",
            ),
        ),
        ToolProfile(
            name="stats.distribution_diagnostics",
            family="research",
            input_kind="stage10_distribution_v1",
            stores=(),
            datasets=(),
        ),
        ToolProfile(
            name="stats.covariance_matrix",
            family="research",
            input_kind="stage10_covariance_v1",
            stores=(),
            datasets=(),
        ),
        ToolProfile(
            name="stats.bootstrap_confidence_interval",
            family="research",
            input_kind="stage10_bootstrap_v1",
            stores=(),
            datasets=(),
        ),
        ToolProfile(
            name="stats.principal_components",
            family="research",
            input_kind="stage10_principal_components_v1",
            stores=(),
            datasets=(),
        ),
    )
    declarations = {item.name: item for item in (*tool_profiles(), *additive)}
    result = tuple(declarations[name] for name in CURRENT_PUBLIC_TOOL_NAMES)
    if tuple(item.name for item in result) != CURRENT_PUBLIC_TOOL_NAMES:
        raise AssertionError("The active public-tool inventory drifted")
    return result


def _family(name: str) -> str:
    prefix = name.split(".", 1)[0]
    if prefix in {"data", "alpha", "stats", "forecast", "news"}:
        return "research"
    return prefix


def _stores(name: str, datasets: tuple[str, ...]) -> tuple[str, ...]:
    if name.startswith(("macro.", "energy.", "rates.")):
        return ("macro",)
    if name.startswith(("market.", "options.")):
        return ("market",)
    if name.startswith("company."):
        return ("company",)
    if name == "news.search":
        return ("news",)
    if name == "research.liquidity_credit_state":
        return ("macro",)
    if datasets:
        raise AssertionError(f"Store routing is missing for {name}")
    return ()


def _input_kind(name: str) -> str:
    if name in LEGACY_TOOL_NAMES:
        return "legacy"
    if name in _SEARCH_TOOLS:
        return "search"
    if name in _SINGLE_SERIES_TOOLS:
        return "single_series"
    if name in _MULTI_SERIES_TOOLS:
        return "multi_series"
    if name in _RESEARCH_TOOLS:
        return "research"
    return "query"


def tool_profiles() -> tuple[ToolProfile, ...]:
    profiles = tuple(
        ToolProfile(
            name=name,
            family=_family(name),
            input_kind=_input_kind(name),
            stores=_stores(name, _DATASETS.get(name, ())),
            datasets=_DATASETS.get(name, ()),
            live_capability=(
                "macro_intraday_live" if name == "macro.get_intraday_releases" else None
            ),
        )
        for name in PUBLIC_TOOL_NAMES
    )
    if len(profiles) != 57 or len({item.name for item in profiles}) != 57:
        raise AssertionError("The Stage 5 public inventory must contain 57 unique names")
    observed = {family: 0 for family in FAMILY_COUNTS}
    for profile in profiles:
        observed[profile.family] += 1
    if observed != FAMILY_COUNTS:
        raise AssertionError("The Stage 5 family inventory drifted")
    return profiles


def _example(profile: ToolProfile, series_example: Mapping[str, Any]) -> dict[str, Any]:
    if profile.input_kind == "search":
        return {"query": "fixture", "as_of": None, "limit": 20}
    if profile.input_kind == "single_series":
        return {"series": copy.deepcopy(dict(series_example)), "parameters": [], "limit": 100}
    if profile.input_kind == "multi_series":
        return {"series": [copy.deepcopy(dict(series_example))], "parameters": [], "limit": 100}
    if profile.input_kind == "research":
        return {
            "series": [copy.deepcopy(dict(series_example))],
            "parameters": [],
            "limit": 100,
            "as_of": None,
            "unsafe_ok": True,
        }
    identifiers = (
        ["us_gdp_real_qoq_saar_advance"]
        if profile.name == "macro.release_surprises"
        else ["fixture"]
    )
    return {
        "identifiers": identifiers,
        "mode": "latest",
        "as_of": None,
        "start_date": None,
        "end_date": None,
        "parameters": [],
        "limit": 100,
    }


def _schema_id(name: str, direction: str) -> str:
    return f"urn:quant-data:tool:{name}:{direction}:1.0.0"


def _versioned_schema_id(
    name: str, direction: str, version: str = "2.0.0"
) -> str:
    return f"urn:quant-data:tool:{name}:{direction}:{version}"


def build_tool_entries(
    legacy_entries: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Generate canonical registry declarations from typed profile metadata."""

    if set(legacy_entries) != set(LEGACY_TOOL_NAMES):
        raise ValueError("Both exact Stage 1 tool declarations are required")
    macro = legacy_entries["macro.get_series"]
    describe = legacy_entries["timeseries.describe"]
    series_schema = copy.deepcopy(dict(macro["output_schema"]))
    series_example = copy.deepcopy(dict(describe["examples"][0]["series"]))
    generated: list[dict[str, Any]] = []
    for profile in tool_profiles():
        legacy = legacy_entries.get(profile.name)
        if legacy is not None:
            input_schema = copy.deepcopy(dict(legacy["input_schema"]))
            output_schema = copy.deepcopy(dict(legacy["output_schema"]))
            examples = copy.deepcopy(list(legacy["examples"]))
            availability = copy.deepcopy(dict(legacy["availability_policy"]))
        else:
            input_schema = typed_input_schema(
                profile.input_kind, series_schema
            )
            output_schema = query_result_schema(profile.name, series_schema)
            examples = [_example(profile, series_example)]
            availability = {
                "modes": ["latest", "as_of", "first_release"],
                "default_date_only_policy": "completed_date",
                "point_in_time_default": "not_established",
            }
        compatibility_status = (
            "recovered_fixture_validated"
            if profile.name in LEGACY_TOOL_NAMES
            else "forward_reconstructed_v1"
        )
        generated.append(
            {
                "id": profile.name,
                "family": profile.family,
                "api_version": "1.0",
                "version": "1.0.0",
                "operation_version": "1.0.0",
                "lifecycle": "stable" if legacy is not None else "experimental",
                "compatibility": {
                    "status": compatibility_status,
                    "predecessor": None,
                },
                "description": f"Offline read-only {profile.name} operation.",
                "assumptions": [
                    "offline_fixture_only",
                    "host_selected_stores",
                    "no_recovered_schema_parity_claim"
                    if legacy is None
                    else "stage1_contract_preserved",
                ],
                "handler": profile.operation_graph_id,
                "operation_graph_id": profile.operation_graph_id,
                "read_only": True,
                "stores": list(profile.stores),
                "datasets": list(profile.datasets),
                "input_type": "TimeSeries" if profile.input_kind in {"single_series", "multi_series", "research"} else "QueryArgumentsV1",
                "input_schema_id": _schema_id(profile.name, "input"),
                "input_schema": input_schema,
                "output_type": "TimeSeries" if profile.name == "macro.get_series" else ("TimeSeriesDescription" if profile.name == "timeseries.describe" else "QueryResultV1"),
                "output_schema_id": _schema_id(profile.name, "output"),
                "output_schema": output_schema,
                "examples": examples,
                "workload_bounds": {
                    "max_rows": 10000,
                    "max_series": 20,
                    "max_operations": 5000000,
                    "max_request_bytes": 8388608,
                    "max_response_bytes": 8388608,
                },
                "cost_model": {
                    "expression": "rows + series + operations",
                    "deterministic": True,
                },
                "timeout_class": "interactive_5s",
                "availability_policy": availability,
                "live_capability": {
                    "possible": profile.live_capability is not None,
                    "capability_id": profile.live_capability,
                    "offline_status": "disabled" if profile.live_capability else "not_applicable",
                },
                "contracts": {
                    "availability": "source_evidenced",
                    "point_in_time": "explicit",
                    "returns": "explicit_or_not_applicable",
                },
                "composable": {
                    "input_types": ["TimeSeries"] if profile.input_kind in {"single_series", "multi_series", "research"} else [],
                    "output_types": ["TimeSeries"] if profile.name == "macro.get_series" else ["QueryResultV1"],
                },
                "observability": "metadata_only",
                "owner": profile.stores[0] if len(profile.stores) == 1 else "tool_platform",
                "review_requirements": ["schema", "semantics", "read_only"],
            }
        )
    return tuple(generated)

def build_additive_tool_entries() -> tuple[dict[str, Any], ...]:
    """Build native tools added after the recovered 57-name compatibility set."""

    from .macro_access import canonical_macro_query_result_schema
    from .market_prices import stage10_market_price_series_schema
    from .market_volume import stage10_market_volume_series_schema

    calendar_name = "macro.get_release_calendar"
    calendar_profile = next(
        item for item in current_tool_profiles() if item.name == calendar_name
    )
    calendar_graph = calendar_profile.operation_graph_id
    calendar_entry = {
        "id": calendar_name,
        "family": calendar_profile.family,
        "api_version": "1.0",
        "version": "1.0.0",
        "operation_version": "1.0.0",
        "lifecycle": "experimental",
        "compatibility": {
            "status": "additive_native_v1",
            "predecessor": None,
        },
        "description": (
            "Read the retained FMP release calendar with explicit latest or "
            "local-capture as-of selection."
        ),
        "assumptions": [
            "host_selected_stores",
            "us_scoped_provider_request",
            "provider_returned_countries_preserved",
            "legacy_and_incremental_calendar_reconciled_by_event_identity",
            "local_capture_availability",
            "first_release_not_applicable_to_schedule_records",
        ],
        "handler": calendar_graph,
        "operation_graph_id": calendar_graph,
        "read_only": True,
        "stores": list(calendar_profile.stores),
        "datasets": list(calendar_profile.datasets),
        "input_type": "MacroReleaseCalendarArgumentsV1",
        "input_schema_id": _versioned_schema_id(
            calendar_name, "input", version="1.0.0"
        ),
        "input_schema": typed_input_schema("macro_release_calendar_v1", {}),
        "output_type": "QueryResultV1",
        "output_schema_id": _versioned_schema_id(
            calendar_name, "output", version="1.0.0"
        ),
        "output_schema": canonical_macro_query_result_schema(
            calendar_name, carries_series=False
        ),
        "examples": [
            {
                "mode": "latest",
                "as_of": None,
                "date_only_policy": "completed_date",
                "limit": 100,
            }
        ],
        "workload_bounds": {
            "max_rows": 10000,
            "max_series": 1,
            "max_operations": 5000000,
            "max_request_bytes": 8388608,
            "max_response_bytes": 8388608,
        },
        "cost_model": {
            "expression": "rows + series + operations",
            "deterministic": True,
        },
        "timeout_class": "interactive_5s",
        "availability_policy": {
            "modes": ["latest", "as_of"],
            "default_date_only_policy": "completed_date",
            "point_in_time_default": "not_applicable",
            "as_of_point_in_time_status": (
                "safe_for_retained_local_captures"
            ),
            "date_bounds": "optional_inclusive_scheduled_event_dates",
            "first_release": "not_applicable_rejected",
        },
        "live_capability": {
            "possible": False,
            "capability_id": None,
            "offline_status": "not_applicable",
        },
        "contracts": {
            "availability": "local_capture",
            "point_in_time": "explicit",
            "returns": "not_applicable",
        },
        "composable": {
            "input_types": [],
            "output_types": ["QueryResultV1"],
        },
        "observability": "metadata_only",
        "owner": "macro",
        "review_requirements": ["schema", "semantics", "read_only"],
    }

    name = "market.get_price_series"
    profile = next(
        item for item in current_tool_profiles() if item.name == name
    )
    series_schema = stage10_market_price_series_schema()
    output_schema = query_result_schema(name, series_schema)
    output_schema["properties"]["series"]["minItems"] = 4
    output_schema["properties"]["series"]["maxItems"] = 4
    operation_graph_id = profile.operation_graph_id
    price_entries = (
        {
            "id": name,
            "family": profile.family,
            "api_version": "1.0",
            "version": "1.0.0",
            "operation_version": "1.0.0",
            "lifecycle": "experimental",
            "compatibility": {
                "status": "additive_native_v1",
                "predecessor": None,
            },
            "description": (
                "Read one ticker's canonical Stage 10 open, high, low, and "
                "close price series."
            ),
            "assumptions": [
                "stage10_canonical_model",
                "host_selected_stores",
                "provider_symbol_ticker",
                "raw_provider_native_ohlc",
                "adjustment_semantics_not_established",
                "session_calendar_not_established",
            ],
            "handler": operation_graph_id,
            "operation_graph_id": operation_graph_id,
            "read_only": True,
            "stores": list(profile.stores),
            "datasets": list(profile.datasets),
            "input_type": "Stage10MarketPriceArgumentsV1",
            "input_schema_id": _versioned_schema_id(
                name, "input", version="1.0.0"
            ),
            "input_schema": typed_input_schema(
                "stage10_market_price_v1", {}
            ),
            "output_type": "QueryResultV1",
            "output_schema_id": _versioned_schema_id(
                name, "output", version="1.0.0"
            ),
            "output_schema": output_schema,
            "examples": [
                {
                    "ticker": "AAPL",
                    "mode": "latest",
                    "as_of": None,
                    "date_only_policy": "completed_date",
                    "limit": 100,
                }
            ],
            "workload_bounds": {
                "max_rows": 10000,
                "max_series": 4,
                "max_operations": 5000000,
                "max_request_bytes": 8388608,
                "max_response_bytes": 8388608,
            },
            "cost_model": {
                "expression": "rows + series + operations",
                "deterministic": True,
            },
            "timeout_class": "interactive_5s",
            "availability_policy": {
                "modes": ["latest", "as_of"],
                "default_date_only_policy": "completed_date",
                "point_in_time_default": "not_applicable",
                "as_of_point_in_time_status": (
                    "safe_for_retained_local_captures"
                ),
                "date_bounds": "optional_inclusive_trade_dates",
            },
            "live_capability": {
                "possible": False,
                "capability_id": None,
                "offline_status": "not_applicable",
            },
            "contracts": {
                "availability": "local_capture",
                "point_in_time": "explicit",
                "returns": "not_applicable_raw_ohlc",
            },
            "composable": {
                "input_types": [],
                "output_types": [
                    "QueryResultV1",
                    "Stage10MarketPriceSeriesV1",
                ],
            },
            "observability": "metadata_only",
            "owner": "market",
            "review_requirements": ["schema", "semantics", "read_only"],
        },
    )
    ticker_name = "market.get_available_ticker"
    ticker_profile = next(
        item for item in current_tool_profiles() if item.name == ticker_name
    )
    ticker_output_schema = query_result_schema(ticker_name, series_schema)
    ticker_output_schema["properties"]["series"]["maxItems"] = 0
    ticker_operation_graph_id = ticker_profile.operation_graph_id
    ticker_entry = {
        "id": ticker_name,
        "family": ticker_profile.family,
        "api_version": "1.0",
        "version": "1.0.0",
        "operation_version": "1.0.0",
        "lifecycle": "experimental",
        "compatibility": {
            "status": "additive_native_v1",
            "predecessor": None,
        },
        "description": (
            "List current Stage 10 tickers that have at least one "
            "retrievable daily-price row."
        ),
        "assumptions": [
            "stage10_canonical_model",
            "host_selected_stores",
            "current_retrievable_price_rows_only",
            "omitted_limit_defaults_to_10000",
            "not_a_live_universe",
            "not_a_historical_point_in_time_universe",
        ],
        "handler": ticker_operation_graph_id,
        "operation_graph_id": ticker_operation_graph_id,
        "read_only": True,
        "stores": list(ticker_profile.stores),
        "datasets": list(ticker_profile.datasets),
        "input_type": "Stage10AvailableTickerArgumentsV1",
        "input_schema_id": _versioned_schema_id(
            ticker_name, "input", version="1.0.0"
        ),
        "input_schema": typed_input_schema(
            "stage10_available_ticker_v1", {}
        ),
        "output_type": "QueryResultV1",
        "output_schema_id": _versioned_schema_id(
            ticker_name, "output", version="1.0.0"
        ),
        "output_schema": ticker_output_schema,
        "examples": [{}],
        "workload_bounds": {
            "max_rows": 10000,
            "max_series": 1,
            "max_operations": 5000000,
            "max_request_bytes": 8388608,
            "max_response_bytes": 8388608,
        },
        "cost_model": {
            "expression": "rows + series + operations",
            "deterministic": True,
        },
        "timeout_class": "interactive_5s",
        "availability_policy": {
            "modes": ["latest"],
            "default_date_only_policy": "not_applicable",
            "point_in_time_default": "not_applicable_current_only",
            "selection": "current_retrievable_stage10_daily_price",
        },
        "live_capability": {
            "possible": False,
            "capability_id": None,
            "offline_status": "not_applicable",
        },
        "contracts": {
            "availability": "current_stored_knowledge",
            "point_in_time": "not_applicable_current_only",
            "returns": "not_applicable",
        },
        "composable": {
            "input_types": [],
            "output_types": ["QueryResultV1"],
        },
        "observability": "metadata_only",
        "owner": "market",
        "review_requirements": ["schema", "semantics", "read_only"],
    }
    volume_name = "market.get_volume_series"
    volume_profile = next(
        item for item in current_tool_profiles() if item.name == volume_name
    )
    volume_schema = stage10_market_volume_series_schema()
    volume_output_schema = query_result_schema(volume_name, volume_schema)
    volume_output_schema["properties"]["series"]["minItems"] = 1
    volume_output_schema["properties"]["series"]["maxItems"] = 1
    volume_graph = volume_profile.operation_graph_id
    volume_entry = {
        "id": volume_name,
        "family": volume_profile.family,
        "api_version": "1.0",
        "version": "1.0.0",
        "operation_version": "1.0.0",
        "lifecycle": "experimental",
        "compatibility": {
            "status": "additive_native_v1",
            "predecessor": None,
        },
        "description": (
            "Read one ticker's canonical Stage 10 provider-native daily "
            "volume series."
        ),
        "assumptions": [
            "stage10_canonical_model",
            "host_selected_stores",
            "provider_symbol_ticker",
            "provider_native_volume",
            "volume_adjustment_semantics_not_established",
            "session_calendar_not_established",
        ],
        "handler": volume_graph,
        "operation_graph_id": volume_graph,
        "read_only": True,
        "stores": list(volume_profile.stores),
        "datasets": list(volume_profile.datasets),
        "input_type": "Stage10MarketVolumeArgumentsV1",
        "input_schema_id": _versioned_schema_id(
            volume_name, "input", version="1.0.0"
        ),
        "input_schema": typed_input_schema("stage10_market_volume_v1", {}),
        "output_type": "QueryResultV1",
        "output_schema_id": _versioned_schema_id(
            volume_name, "output", version="1.0.0"
        ),
        "output_schema": volume_output_schema,
        "examples": [
            {
                "ticker": "AAPL",
                "mode": "latest",
                "as_of": None,
                "date_only_policy": "completed_date",
                "limit": 100,
            }
        ],
        "workload_bounds": {
            "max_rows": 10000,
            "max_series": 1,
            "max_operations": 5000000,
            "max_request_bytes": 8388608,
            "max_response_bytes": 8388608,
        },
        "cost_model": {
            "expression": "rows + series + operations",
            "deterministic": True,
        },
        "timeout_class": "interactive_5s",
        "availability_policy": {
            "modes": ["latest", "as_of"],
            "default_date_only_policy": "completed_date",
            "point_in_time_default": "not_applicable",
            "as_of_point_in_time_status": (
                "safe_for_retained_local_captures"
            ),
            "date_bounds": "optional_inclusive_trade_dates",
        },
        "live_capability": {
            "possible": False,
            "capability_id": None,
            "offline_status": "not_applicable",
        },
        "contracts": {
            "availability": "local_capture",
            "point_in_time": "explicit",
            "returns": "not_applicable_provider_native_volume",
        },
        "composable": {
            "input_types": [],
            "output_types": ["QueryResultV1", "Stage10MarketVolumeSeriesV1"],
        },
        "observability": "metadata_only",
        "owner": "market",
        "review_requirements": ["schema", "semantics", "read_only"],
    }
    return (
        calendar_entry,
        ticker_entry,
        *price_entries,
        volume_entry,
        *_build_analysis_foundation_entries(),
    )


def _build_analysis_foundation_entries() -> tuple[dict[str, Any], ...]:
    """Build deterministic, store-free statistics over typed Stage 10 returns."""

    from .market_statistics import (
        stage10_market_return_example,
        stage10_market_statistic_series_schema,
    )

    series_schema = stage10_market_statistic_series_schema()
    examples = {
        "AAPL": stage10_market_return_example("AAPL"),
        "MSFT": stage10_market_return_example("MSFT"),
    }
    contracts = (
        {
            "name": "stats.distribution_diagnostics",
            "input_kind": "stage10_distribution_v1",
            "input_type": "Stage10DistributionArgumentsV1",
            "description": (
                "Compute deterministic distribution, quantile, median absolute "
                "deviation, skewness, kurtosis, and Jarque-Bera diagnostics."
            ),
            "assumptions": (
                "type7_sample_quantiles",
                "population_central_moments",
                "jarque_bera_asymptotic_chi_square_reference",
            ),
            "example": {"series": examples["AAPL"], "limit": 100},
            "max_series": 1,
            "estimator": "deterministic_distribution_diagnostics_v1",
        },
        {
            "name": "stats.covariance_matrix",
            "input_kind": "stage10_covariance_v1",
            "input_type": "Stage10CovarianceArgumentsV1",
            "description": (
                "Estimate sample covariance and correlation matrices over "
                "outer-aligned, joint-complete Stage 10 returns."
            ),
            "assumptions": (
                "outer_alignment_joint_complete_rows",
                "sample_covariance_denominator_n_minus_one",
                "no_pairwise_sample_switching",
            ),
            "example": {
                "series": [examples["AAPL"], examples["MSFT"]],
                "limit": 100,
            },
            "max_series": 20,
            "estimator": "joint_complete_sample_covariance_v1",
        },
        {
            "name": "stats.bootstrap_confidence_interval",
            "input_kind": "stage10_bootstrap_v1",
            "input_type": "Stage10BootstrapArgumentsV1",
            "description": (
                "Compute an explicitly seeded deterministic IID percentile "
                "bootstrap confidence interval for a mean or median."
            ),
            "assumptions": (
                "iid_resampling_with_replacement",
                "required_unsigned_32_bit_seed",
                "fixed_splitmix64_prng",
                "type7_percentile_interval",
            ),
            "example": {
                "series": examples["AAPL"],
                "statistic": "mean",
                "seed": 1729,
                "replicates": 1000,
                "confidence_level": "0.95",
                "limit": 100,
            },
            "max_series": 1,
            "estimator": "seeded_iid_percentile_bootstrap_v1",
        },
        {
            "name": "stats.principal_components",
            "input_kind": "stage10_principal_components_v1",
            "input_type": "Stage10PrincipalComponentsArgumentsV1",
            "description": (
                "Compute deterministic covariance- or correlation-basis PCA "
                "with canonical eigenvalue ordering and eigenvector signs."
            ),
            "assumptions": (
                "outer_alignment_joint_complete_rows",
                "decimal_symmetric_jacobi_eigendecomposition",
                "descending_eigenvalue_order",
                "canonical_largest_absolute_loading_positive",
            ),
            "example": {
                "series": [examples["AAPL"], examples["MSFT"]],
                "basis": "correlation",
                "components": 2,
                "include_scores": True,
                "limit": 100,
            },
            "max_series": 20,
            "estimator": "deterministic_symmetric_jacobi_pca_v1",
        },
    )
    entries: list[dict[str, Any]] = []
    for contract in contracts:
        name = str(contract["name"])
        profile = next(
            item for item in current_tool_profiles() if item.name == name
        )
        graph = profile.operation_graph_id
        output_schema = query_result_schema(name, series_schema)
        output_schema["properties"]["series"]["maxItems"] = 0
        entries.append(
            {
                "id": name,
                "family": "research",
                "api_version": "1.0",
                "version": "1.0.0",
                "operation_version": "1.0.0",
                "lifecycle": "experimental",
                "compatibility": {
                    "status": "additive_native_v1",
                    "predecessor": None,
                },
                "description": contract["description"],
                "assumptions": [
                    "caller_supplied_stage10_trailing_return_series",
                    "no_store_access",
                    "strict_return_contract_compatibility",
                    "truncated_samples_rejected",
                    "explicit_missingness_no_fill",
                    "client_snapshot_coherence_not_established",
                    *contract["assumptions"],
                ],
                "handler": graph,
                "operation_graph_id": graph,
                "read_only": True,
                "stores": [],
                "datasets": [],
                "input_type": contract["input_type"],
                "input_schema_id": _versioned_schema_id(
                    name, "input", version="1.0.0"
                ),
                "input_schema": typed_input_schema(
                    contract["input_kind"], series_schema
                ),
                "output_type": "QueryResultV1",
                "output_schema_id": _versioned_schema_id(
                    name, "output", version="1.0.0"
                ),
                "output_schema": output_schema,
                "examples": [contract["example"]],
                "workload_bounds": {
                    "max_rows": 10000,
                    "max_series": contract["max_series"],
                    "max_operations": 5000000,
                    "max_request_bytes": 8388608,
                    "max_response_bytes": 8388608,
                },
                "cost_model": {
                    "expression": "rows + series + operations",
                    "deterministic": True,
                },
                "timeout_class": "interactive_5s",
                "availability_policy": {
                    "modes": ["inherited_from_typed_input"],
                    "point_in_time_default": "inherited_and_revalidated",
                },
                "live_capability": {
                    "possible": False,
                    "capability_id": None,
                    "offline_status": "not_applicable",
                },
                "contracts": {
                    "availability": "inherited_from_typed_input",
                    "point_in_time": "exact_input_contract",
                    "returns": "stage10_trailing_return_series",
                },
                "composable": {
                    "input_types": ["Stage10MarketReturnSeriesV2"],
                    "output_types": ["QueryResultV1"],
                },
                "observability": "metadata_only",
                "owner": "tool_platform",
                "review_requirements": ["schema", "semantics", "read_only"],
            }
        )
    return tuple(entries)


def build_current_tool_entries(
    legacy_entries: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Merge additive native tools without changing frozen recovered entries."""

    declarations = {
        item["id"]: item
        for item in (
            *build_tool_entries(legacy_entries),
            *build_additive_tool_entries(),
        )
    }
    result = tuple(declarations[name] for name in CURRENT_PUBLIC_TOOL_NAMES)
    if tuple(item["id"] for item in result) != CURRENT_PUBLIC_TOOL_NAMES:
        raise AssertionError("The active public-tool declaration order drifted")
    return result



def schema_catalog(entries: tuple[Mapping[str, Any], ...]) -> dict[str, Any]:
    contracts: list[dict[str, Any]] = []
    for entry in entries:
        for direction in ("input", "output"):
            schema = copy.deepcopy(dict(entry[f"{direction}_schema"]))
            schema["$schema"] = SCHEMA_DIALECT
            schema["$id"] = entry[f"{direction}_schema_id"]
            contracts.append(
                {
                    "id": entry[f"{direction}_schema_id"],
                    "tool": entry["id"],
                    "direction": direction,
                    "schema": schema,
                }
            )
    return {
        "schema_id": CATALOG_ID,
        "schema_version": CATALOG_VERSION,
        "dialect": SCHEMA_DIALECT,
        "contracts": contracts,
    }


def build_tool_version_policies() -> tuple[dict[str, Any], ...]:
    """Build additive v2 variants without mutating frozen v1 entries."""

    from .macro_access import canonical_macro_query_result_schema
    from .market_returns import stage10_market_return_series_schema
    from .market_statistics import (
        stage10_market_return_example,
        stage10_market_statistic_series_schema,
    )
    from .technical_indicator_adapter import (
        stage10_technical_indicator_example,
        stage10_technical_indicator_input_series_schema,
        stage10_technical_indicator_output_series_schema,
        stage10_technical_indicator_output_series_schema_v21,
        stage10_technical_indicator_output_series_schema_v22,
        stage10_technical_indicator_output_series_schema_v23,
        stage10_technical_indicator_output_series_schema_v24,
        stage10_technical_indicator_output_series_schema_v25,
        stage10_technical_indicator_output_series_schema_v26,
        stage10_technical_indicator_output_series_schema_v27,
    )

    return_series_schema = stage10_market_return_series_schema()
    analysis_series_schema = stage10_market_statistic_series_schema()
    result: list[dict[str, Any]] = []
    macro_datasets = [
        "fixture.macro.rtdsm_employ",
        "fixture.macro.rtdsm_employ_evidence",
        "fixture.macro.stage3_catalog",
        "macro.official_vintages",
        "macro.official_vintages_evidence",
    ]
    macro_contracts = {
        "macro.search_series": {
            "input_kind": "canonical_macro_search_v2",
            "input_type": "CanonicalMacroSearchArgumentsV2",
            "description": (
                "Search the current retained generic and official-vintage "
                "canonical macro catalog."
            ),
            "example": {"query": "gdp", "limit": 100},
            "max_rows": 500,
            "carries_series": False,
        },
        "macro.describe_series": {
            "input_kind": "canonical_macro_describe_v2",
            "input_type": "CanonicalMacroDescribeArgumentsV2",
            "description": (
                "Describe one exact current retained generic or "
                "official-vintage canonical macro series."
            ),
            "example": {
                "series_id": "macro.gdp.real_qoq_saar_pct",
            },
            "max_rows": 1,
            "carries_series": False,
        },
        "macro.get_series": {
            "input_kind": "canonical_macro_series_v2",
            "input_type": "CanonicalMacroSeriesArgumentsV2",
            "description": (
                "Read one canonical generic or official-vintage macro series "
                "with explicit latest, as-of, or evidenced first-release selection."
            ),
            "example": {
                "series_id": "macro.gdp.real_qoq_saar_pct",
                "mode": "as_of",
                "as_of": "2026-07-31T23:59:59Z",
                "date_only_policy": "completed_date",
                "limit": 100,
            },
            "max_rows": 10000,
            "carries_series": True,
        },
    }
    for name in VERSIONED_CANONICAL_MACRO_TOOLS:
        contract = macro_contracts[name]
        graph = f"tool_platform.{name}.v2"
        variant = {
            "id": name,
            "family": "macro",
            "api_version": "1.0",
            "version": "2.0.0",
            "operation_version": "2.0.0",
            "lifecycle": "experimental",
            "compatibility": {
                "status": "successor_breaking_v2",
                "predecessor": "1.0.0",
            },
            "description": contract["description"],
            "assumptions": [
                "host_selected_stores",
                "official_series_ids_use_isolated_official_vintage_model",
                "all_other_series_ids_use_generic_version_core",
                "no_cross_model_fallback",
                "source_native_vintage_precision",
                "current_retained_catalog_not_historical_catalog_as_of",
            ],
            "handler": graph,
            "operation_graph_id": graph,
            "read_only": True,
            "stores": ["macro"],
            "datasets": macro_datasets,
            "input_type": contract["input_type"],
            "input_schema_id": _versioned_schema_id(name, "input"),
            "input_schema": typed_input_schema(contract["input_kind"], {}),
            "output_type": (
                "QueryResultV1WithMacroTimeSeriesV2"
                if contract["carries_series"]
                else "QueryResultV1"
            ),
            "output_schema_id": _versioned_schema_id(name, "output"),
            "output_schema": canonical_macro_query_result_schema(
                name, carries_series=contract["carries_series"]
            ),
            "examples": [contract["example"]],
            "workload_bounds": {
                "max_rows": contract["max_rows"],
                "max_series": 1,
                "max_operations": 5000000,
                "max_request_bytes": 8388608,
                "max_response_bytes": 8388608,
            },
            "cost_model": {
                "expression": "rows + series + operations",
                "deterministic": True,
            },
            "timeout_class": "interactive_5s",
            "availability_policy": (
                {
                    "modes": ["latest", "as_of", "first_release"],
                    "default_date_only_policy": "completed_date",
                    "point_in_time_default": "not_applicable",
                    "as_of_point_in_time_status": "safe_for_stored_availability",
                    "first_release": "explicit_flag_and_evidence_required",
                    "date_bounds": "optional_inclusive_period_start",
                }
                if contract["carries_series"]
                else {
                    "modes": ["current_retained_catalog"],
                    "point_in_time_default": "not_applicable_current_only",
                }
            ),
            "live_capability": {
                "possible": False,
                "capability_id": None,
                "offline_status": "not_applicable",
            },
            "contracts": {
                "availability": (
                    "source_native_stored_availability"
                    if contract["carries_series"]
                    else "current_retained_catalog"
                ),
                "point_in_time": (
                    "explicit" if contract["carries_series"] else "not_applicable"
                ),
                "returns": "not_applicable",
            },
            "composable": {
                "input_types": [],
                "output_types": (
                    ["QueryResultV1", "MacroTimeSeriesV2"]
                    if contract["carries_series"]
                    else ["QueryResultV1"]
                ),
            },
            "observability": "metadata_only",
            "owner": "macro",
            "review_requirements": ["schema", "semantics", "read_only"],
        }
        result.append(
            {
                "tool": name,
                "default_version": "1.0.0",
                "selector_field": "tool_version",
                "variants": [variant],
                "deprecations": [
                    {
                        "version": "1.0.0",
                        "code": "tool_version_deprecated",
                        "message": (
                            f"{name} version 1.0.0 remains available for compatibility; "
                            "select version 2.0.0 for canonical macro access."
                        ),
                        "replacement": {"tool": name, "version": "2.0.0"},
                        "removal": {"status": "not_scheduled", "milestone": None},
                    }
                ],
            }
        )
    for name in VERSIONED_MARKET_RETURN_TOOLS:
        direction = "forward" if name == "market.get_forward_returns" else "trailing"
        operation_graph_id = f"tool_platform.{name}.v2"
        variant = {
            "id": name,
            "family": "market",
            "api_version": "1.0",
            "version": "2.0.0",
            "operation_version": "2.0.0",
            "lifecycle": "experimental",
            "compatibility": {
                "status": "successor_breaking_v2",
                "predecessor": "1.0.0",
            },
            "description": (
                "Read-only Stage 10 close-to-close forward return operation."
                if direction == "forward"
                else "Read-only Stage 10 close-to-close trailing return operation."
            ),
            "assumptions": [
                "stage10_canonical_model",
                "host_selected_stores",
                "close_to_close_prices",
                "observed_row_horizon",
                "adjustment_semantics_not_established",
                "session_calendar_not_established",
            ],
            "handler": operation_graph_id,
            "operation_graph_id": operation_graph_id,
            "read_only": True,
            "stores": ["market"],
            "datasets": [
                "market.stage10.daily_prices",
                "market.stage10.source_evidence",
                "market.stage10.instruments",
            ],
            "input_type": "Stage10MarketReturnArgumentsV2",
            "input_schema_id": _versioned_schema_id(name, "input"),
            "input_schema": typed_input_schema("stage10_market_return_v2", {}),
            "output_type": "QueryResultV1",
            "output_schema_id": _versioned_schema_id(name, "output"),
            "output_schema": query_result_schema(name, return_series_schema),
            "examples": [
                {
                    "identifier": "AAPL",
                    "identifier_kind": "provider_symbol",
                    "start_date": "2026-08-10",
                    "end_date": "2026-08-12",
                    "mode": "latest",
                    "as_of": None,
                    "date_only_policy": "completed_date",
                    "method": "simple",
                    "horizon": 1,
                    "limit": 100,
                }
            ],
            "workload_bounds": {
                "max_rows": 10000,
                "max_series": 1,
                "max_operations": 5000000,
                "max_request_bytes": 8388608,
                "max_response_bytes": 8388608,
            },
            "cost_model": {
                "expression": "rows + series + operations",
                "deterministic": True,
            },
            "timeout_class": "interactive_5s",
            "availability_policy": {
                "modes": ["latest", "as_of"],
                "default_date_only_policy": "completed_date",
                "point_in_time_default": "not_applicable",
                "as_of_point_in_time_status": "safe_for_retained_local_captures",
            },
            "live_capability": {
                "possible": False,
                "capability_id": None,
                "offline_status": "not_applicable",
            },
            "contracts": {
                "availability": "local_capture",
                "point_in_time": "explicit",
                "returns": f"close_to_close_{direction}_observed_rows",
            },
            "composable": {
                "input_types": [],
                "output_types": ["QueryResultV1"],
            },
            "observability": "metadata_only",
            "owner": "market",
            "review_requirements": ["schema", "semantics", "read_only"],
        }
        result.append(
            {
                "tool": name,
                "default_version": "1.0.0",
                "selector_field": "tool_version",
                "variants": [variant],
                "deprecations": [
                    {
                        "version": "1.0.0",
                        "code": "tool_version_deprecated",
                        "message": (
                            f"{name} version 1.0.0 remains available for compatibility; "
                            "select version 2.0.0 for typed Stage 10 returns."
                        ),
                        "replacement": {"tool": name, "version": "2.0.0"},
                        "removal": {"status": "not_scheduled", "milestone": None},
                    }
                ],
            }
        )

    name = "market.technical_indicators"
    operation_graph_id = "tool_platform.market.technical_indicators.v2"
    indicator_output_schema = query_result_schema(
        name, stage10_technical_indicator_output_series_schema()
    )
    indicator_output_schema["properties"]["series"]["minItems"] = 1
    indicator_output_schema["properties"]["series"]["maxItems"] = 3
    indicator_output_schema_v21 = query_result_schema(
        name, stage10_technical_indicator_output_series_schema_v21()
    )
    indicator_output_schema_v21["properties"]["series"]["minItems"] = 1
    indicator_output_schema_v21["properties"]["series"]["maxItems"] = 5
    indicator_output_schema_v22 = query_result_schema(
        name, stage10_technical_indicator_output_series_schema_v22()
    )
    indicator_output_schema_v22["properties"]["series"]["minItems"] = 1
    indicator_output_schema_v22["properties"]["series"]["maxItems"] = 10
    indicator_output_schema_v23 = query_result_schema(
        name, stage10_technical_indicator_output_series_schema_v23()
    )
    indicator_output_schema_v23["properties"]["series"]["minItems"] = 1
    indicator_output_schema_v23["properties"]["series"]["maxItems"] = 10
    indicator_output_schema_v24 = query_result_schema(
        name, stage10_technical_indicator_output_series_schema_v24()
    )
    indicator_output_schema_v24["properties"]["series"]["minItems"] = 1
    indicator_output_schema_v24["properties"]["series"]["maxItems"] = 10
    indicator_output_schema_v25 = query_result_schema(
        name, stage10_technical_indicator_output_series_schema_v25()
    )
    indicator_output_schema_v25["properties"]["series"]["minItems"] = 1
    indicator_output_schema_v25["properties"]["series"]["maxItems"] = 10
    indicator_output_schema_v26 = query_result_schema(
        name, stage10_technical_indicator_output_series_schema_v26()
    )
    indicator_output_schema_v26["properties"]["series"]["minItems"] = 1
    indicator_output_schema_v26["properties"]["series"]["maxItems"] = 10
    indicator_output_schema_v27 = query_result_schema(
        name, stage10_technical_indicator_output_series_schema_v27()
    )
    indicator_output_schema_v27["properties"]["series"]["minItems"] = 1
    indicator_output_schema_v27["properties"]["series"]["maxItems"] = 10
    indicator_variant = {
        "id": name,
        "family": "market",
        "api_version": "1.0",
        "version": "2.0.0",
        "operation_version": "2.0.0",
        "lifecycle": "experimental",
        "compatibility": {
            "status": "successor_breaking_v2",
            "predecessor": "1.0.0",
        },
        "description": (
            "Calculate one deterministic SMA, EMA, volatility, momentum, "
            "trend, channel, oscillator, or volume-derived indicator over "
            "caller-supplied typed Stage 10 OHLCV series."
        ),
        "assumptions": [
            "caller_supplied_stage10_raw_ohlcv_series",
            "one_indicator_specification_per_call",
            "no_store_access",
            "strict_instrument_selection_and_period_grid_compatibility",
            "no_fill_or_session_inference",
            "sample_standard_deviation_for_rolling_dispersion",
            "sma_seeded_ema_and_wilder_recursions",
            "recursive_state_resets_after_missing_input",
            "raw_price_adjustment_semantics_not_established",
            "provider_native_volume_not_normalized",
        ],
        "handler": operation_graph_id,
        "operation_graph_id": operation_graph_id,
        "read_only": True,
        "stores": [],
        "datasets": [],
        "input_type": "Stage10TechnicalIndicatorArgumentsV2",
        "input_schema_id": _versioned_schema_id(name, "input"),
        "input_schema": typed_input_schema(
            "stage10_technical_indicator_v2",
            stage10_technical_indicator_input_series_schema(),
        ),
        "output_type": "QueryResultV1",
        "output_schema_id": _versioned_schema_id(name, "output"),
        "output_schema": indicator_output_schema,
        "examples": [
            {
                "series": [stage10_technical_indicator_example()],
                "indicator": "sma",
                "window": 3,
                "fast_window": None,
                "slow_window": None,
                "signal_window": None,
                "standard_deviation_multiplier": None,
                "limit": 100,
            }
        ],
        "workload_bounds": {
            "max_rows": 10000,
            "max_series": 5,
            "max_operations": 5000000,
            "max_request_bytes": 8388608,
            "max_response_bytes": 8388608,
        },
        "cost_model": {
            "expression": "rows + series + operations",
            "deterministic": True,
        },
        "timeout_class": "interactive_5s",
        "availability_policy": {
            "modes": ["inherited_from_typed_input"],
            "point_in_time_default": "inherited_and_revalidated",
            "missingness": "explicit_warmup_or_undefined_points",
        },
        "live_capability": {
            "possible": False,
            "capability_id": None,
            "offline_status": "not_applicable",
        },
        "contracts": {
            "availability": "conservative_from_input_contributors",
            "point_in_time": "exact_input_contract",
            "returns": "not_applicable_raw_ohlcv_transform",
        },
        "composable": {
            "input_types": [
                "Stage10MarketPriceSeriesV1",
                "Stage10MarketVolumeSeriesV1",
            ],
            "output_types": [
                "QueryResultV1",
                "Stage10TechnicalIndicatorSeriesV2",
            ],
        },
        "observability": "metadata_only",
        "owner": "tool_platform",
        "review_requirements": ["schema", "semantics", "read_only"],
    }
    indicator_variant_v21 = {
        **copy.deepcopy(indicator_variant),
        "version": "2.1.0",
        "operation_version": "2.1.0",
        "compatibility": {
            "status": "additive_successor_v2_1",
            "predecessor": "2.0.0",
        },
        "description": (
            "Calculate one deterministic typed OHLCV indicator, including "
            "the causal clustering-based SuperTrend AI adaptation."
        ),
        "assumptions": [
            *indicator_variant["assumptions"],
            "supertrend_factor_grid_has_three_through_101_candidates",
            "supertrend_cluster_selection_is_best_average_or_worst",
            "supertrend_prefix_results_are_causal_and_range_invariant",
            "empty_kmeans_clusters_retain_their_prior_centroids",
            "adaptive_trend_tests_the_updated_trailing_bands",
            "declared_input_limit_replaces_pine_historical_bars_control",
            "kmeans_convergence_retains_pine_default_iteration_cap",
            "tradingview_colors_labels_and_dashboard_are_excluded",
        ],
        "handler": "tool_platform.market.technical_indicators.v2_1",
        "operation_graph_id": (
            "tool_platform.market.technical_indicators.v2_1"
        ),
        "input_type": "Stage10TechnicalIndicatorArgumentsV21",
        "input_schema_id": _versioned_schema_id(
            name, "input", version="2.1.0"
        ),
        "input_schema": typed_input_schema(
            "stage10_technical_indicator_v2_1",
            stage10_technical_indicator_input_series_schema(),
        ),
        "output_schema_id": _versioned_schema_id(
            name, "output", version="2.1.0"
        ),
        "output_schema": indicator_output_schema_v21,
        "examples": [
            {
                "series": [stage10_technical_indicator_example()],
                "indicator": "sma",
                "window": 3,
                "fast_window": None,
                "slow_window": None,
                "signal_window": None,
                "standard_deviation_multiplier": None,
                "minimum_factor": None,
                "maximum_factor": None,
                "factor_step": None,
                "performance_memory": None,
                "cluster": None,
                "limit": 100,
            }
        ],
        "workload_bounds": {
            **indicator_variant["workload_bounds"],
            "max_operations": 5_000_000,
        },
        "composable": {
            **indicator_variant["composable"],
            "output_types": [
                "QueryResultV1",
                "Stage10TechnicalIndicatorSeriesV21",
            ],
        },
    }
    indicator_variant_v22 = {
        **copy.deepcopy(indicator_variant_v21),
        "version": "2.2.0",
        "operation_version": "2.2.0",
        "compatibility": {
            "status": "additive_successor_v2_2",
            "predecessor": "2.1.0",
        },
        "description": (
            "Calculate one deterministic typed OHLCV indicator, including "
            "the causal BOSWaves swing-structure forecast adaptation."
        ),
        "assumptions": [
            *indicator_variant_v21["assumptions"],
            "swing_direction_is_trailing_window_extreme_state",
            "swing_low_test_wins_same_bar_extreme_ties",
            "pivot_confirmation_is_a_one_bar_causal_event",
            "forecast_history_retains_three_through_twenty_switch_samples",
            "weighted_average_or_median_forecast_aggregation_is_explicit",
            "swing_forecast_variance_is_unweighted_population_variance",
            "swing_forecast_uses_fixed_wilder_atr_200",
            "swing_forecast_is_causal_unrolled_latest_bar_adaptation",
            "forward_bar_position_and_tradingview_drawings_are_excluded",
            "support_resistance_object_state_and_alerts_are_excluded",
        ],
        "handler": "tool_platform.market.technical_indicators.v2_2",
        "operation_graph_id": (
            "tool_platform.market.technical_indicators.v2_2"
        ),
        "input_type": "Stage10TechnicalIndicatorArgumentsV22",
        "input_schema_id": _versioned_schema_id(
            name, "input", version="2.2.0"
        ),
        "input_schema": typed_input_schema(
            "stage10_technical_indicator_v2_2",
            stage10_technical_indicator_input_series_schema(),
        ),
        "output_schema_id": _versioned_schema_id(
            name, "output", version="2.2.0"
        ),
        "output_schema": indicator_output_schema_v22,
        "examples": [
            {
                **copy.deepcopy(indicator_variant_v21["examples"][0]),
                "sample_count": None,
                "aggregation_method": None,
            }
        ],
        "composable": {
            **indicator_variant_v21["composable"],
            "output_types": [
                "QueryResultV1",
                "Stage10TechnicalIndicatorSeriesV22",
            ],
        },
    }
    indicator_variant_v23 = {
        **copy.deepcopy(indicator_variant_v22),
        "version": "2.3.0",
        "operation_version": "2.3.0",
        "compatibility": {
            "status": "additive_successor_v2_3",
            "predecessor": "2.2.0",
        },
        "description": (
            "Calculate one deterministic typed OHLCV indicator, including "
            "the Pine-compatible KDJ oscillator."
        ),
        "assumptions": [
            *indicator_variant_v22["assumptions"],
            "kdj_rsv_uses_full_rolling_high_low_window",
            "kdj_k_and_d_use_bcwsma_with_signal_weight_one",
            "kdj_bcwsma_nz_previous_initializes_and_restarts_at_zero",
            "kdj_j_is_three_k_minus_two_d_without_clamping",
            "kdj_zero_price_range_is_explicit_missingness",
            "kdj_tradingview_colors_background_and_reference_lines_are_excluded",
        ],
        "handler": "tool_platform.market.technical_indicators.v2_3",
        "operation_graph_id": (
            "tool_platform.market.technical_indicators.v2_3"
        ),
        "input_type": "Stage10TechnicalIndicatorArgumentsV23",
        "input_schema_id": _versioned_schema_id(
            name, "input", version="2.3.0"
        ),
        "input_schema": typed_input_schema(
            "stage10_technical_indicator_v2_3",
            stage10_technical_indicator_input_series_schema(),
        ),
        "output_schema_id": _versioned_schema_id(
            name, "output", version="2.3.0"
        ),
        "output_schema": indicator_output_schema_v23,
        "examples": [
            copy.deepcopy(indicator_variant_v22["examples"][0])
        ],
        "composable": {
            **indicator_variant_v22["composable"],
            "output_types": [
                "QueryResultV1",
                "Stage10TechnicalIndicatorSeriesV23",
            ],
        },
    }
    indicator_variant_v24 = {
        **copy.deepcopy(indicator_variant_v23),
        "version": "2.4.0",
        "operation_version": "2.4.0",
        "compatibility": {
            "status": "additive_successor_v2_4",
            "predecessor": "2.3.0",
        },
        "description": (
            "Calculate one deterministic typed OHLCV indicator, including "
            "the Pine-compatible Williams Vix Fix volatility oscillator."
        ),
        "assumptions": [
            *indicator_variant_v23["assumptions"],
            "williams_vix_fix_uses_full_rolling_highest_close_window",
            "williams_vix_fix_requires_close_and_low_series",
            "williams_vix_fix_zero_highest_close_is_explicit_missingness",
            "williams_vix_fix_bollinger_threshold_uses_population_standard_deviation",
            "williams_vix_fix_percentile_thresholds_use_full_rolling_windows",
            "williams_vix_fix_outputs_wvf_upper_band_range_high_and_range_low",
            "williams_vix_fix_unplotted_lower_band_is_excluded",
            "williams_vix_fix_display_toggles_colors_and_plot_styles_are_excluded",
        ],
        "handler": "tool_platform.market.technical_indicators.v2_4",
        "operation_graph_id": (
            "tool_platform.market.technical_indicators.v2_4"
        ),
        "input_type": "Stage10TechnicalIndicatorArgumentsV24",
        "input_schema_id": _versioned_schema_id(
            name, "input", version="2.4.0"
        ),
        "input_schema": typed_input_schema(
            "stage10_technical_indicator_v2_4",
            stage10_technical_indicator_input_series_schema(),
        ),
        "output_schema_id": _versioned_schema_id(
            name, "output", version="2.4.0"
        ),
        "output_schema": indicator_output_schema_v24,
        "examples": [
            {
                **copy.deepcopy(indicator_variant_v23["examples"][0]),
                "percentile_window": None,
                "percentile_high_factor": None,
                "percentile_low_factor": None,
            }
        ],
        "composable": {
            **indicator_variant_v23["composable"],
            "output_types": [
                "QueryResultV1",
                "Stage10TechnicalIndicatorSeriesV24",
            ],
        },
    }
    indicator_variant_v25 = {
        **copy.deepcopy(indicator_variant_v24),
        "version": "2.5.0",
        "operation_version": "2.5.0",
        "compatibility": {
            "status": "additive_successor_v2_5",
            "predecessor": "2.4.0",
        },
        "description": (
            "Calculate one deterministic typed OHLCV indicator, including "
            "the Pine-compatible LazyBear WaveTrend oscillator and crosses."
        ),
        "assumptions": [
            *indicator_variant_v24["assumptions"],
            "wavetrend_average_price_is_high_low_close_divided_by_three",
            "wavetrend_esa_and_deviation_use_sma_seeded_ema_recursions",
            "wavetrend_channel_index_uses_fixed_zero_point_zero_one_five_scaling",
            "wavetrend_zero_smoothed_deviation_is_explicit_missingness",
            "wavetrend_primary_line_uses_the_explicit_average_window",
            "wavetrend_signal_line_uses_a_fixed_full_four_bar_sma",
            "wavetrend_cross_signal_is_one_bullish_minus_one_bearish_zero_otherwise",
            "wavetrend_reference_levels_colors_markers_and_bar_colors_are_excluded",
        ],
        "handler": "tool_platform.market.technical_indicators.v2_5",
        "operation_graph_id": (
            "tool_platform.market.technical_indicators.v2_5"
        ),
        "input_type": "Stage10TechnicalIndicatorArgumentsV25",
        "input_schema_id": _versioned_schema_id(
            name, "input", version="2.5.0"
        ),
        "input_schema": typed_input_schema(
            "stage10_technical_indicator_v2_5",
            stage10_technical_indicator_input_series_schema(),
        ),
        "output_schema_id": _versioned_schema_id(
            name, "output", version="2.5.0"
        ),
        "output_schema": indicator_output_schema_v25,
        "examples": [copy.deepcopy(indicator_variant_v24["examples"][0])],
        "composable": {
            **indicator_variant_v24["composable"],
            "output_types": [
                "QueryResultV1",
                "Stage10TechnicalIndicatorSeriesV25",
            ],
        },
    }
    indicator_variant_v26 = {
        **copy.deepcopy(indicator_variant_v25),
        "version": "2.6.0",
        "operation_version": "2.6.0",
        "compatibility": {
            "status": "additive_successor_v2_6",
            "predecessor": "2.5.0",
        },
        "description": (
            "Calculate one deterministic typed OHLCV indicator, including "
            "Pine-compatible Parabolic SAR."
        ),
        "assumptions": [
            *indicator_variant_v25["assumptions"],
            "parabolic_sar_initial_trend_compares_current_and_previous_close",
            "parabolic_sar_initial_extreme_and_stop_use_current_and_previous_ohlc",
            "parabolic_sar_acceleration_increments_on_new_extremes_and_caps_at_maximum",
            "parabolic_sar_reversal_resets_acceleration_and_extreme",
            "parabolic_sar_stop_is_clamped_against_the_prior_two_lows_or_highs",
            "parabolic_sar_missing_ohlc_resets_recursive_state",
            "parabolic_sar_plot_cross_color_timeframe_and_gap_presentation_are_excluded",
        ],
        "handler": "tool_platform.market.technical_indicators.v2_6",
        "operation_graph_id": (
            "tool_platform.market.technical_indicators.v2_6"
        ),
        "input_type": "Stage10TechnicalIndicatorArgumentsV26",
        "input_schema_id": _versioned_schema_id(
            name, "input", version="2.6.0"
        ),
        "input_schema": typed_input_schema(
            "stage10_technical_indicator_v2_6",
            stage10_technical_indicator_input_series_schema(),
        ),
        "output_schema_id": _versioned_schema_id(
            name, "output", version="2.6.0"
        ),
        "output_schema": indicator_output_schema_v26,
        "examples": [
            {
                **copy.deepcopy(indicator_variant_v25["examples"][0]),
                "start": None,
                "increment": None,
                "maximum": None,
            }
        ],
        "composable": {
            **indicator_variant_v25["composable"],
            "output_types": [
                "QueryResultV1",
                "Stage10TechnicalIndicatorSeriesV26",
            ],
        },
    }
    indicator_variant_v27 = {
        **copy.deepcopy(indicator_variant_v26),
        "version": "2.7.0",
        "operation_version": "2.7.0",
        "compatibility": {
            "status": "additive_successor_v2_7",
            "predecessor": "2.6.0",
        },
        "description": (
            "Calculate one deterministic typed OHLCV indicator, including a "
            "causal rolling ordinary-least-squares regression line."
        ),
        "assumptions": [
            *indicator_variant_v26["assumptions"],
            "rolling_regression_line_uses_close_only",
            "rolling_regression_line_fits_x_zero_through_window_minus_one",
            "rolling_regression_line_emits_fitted_value_at_current_right_edge",
            "rolling_regression_line_requires_a_complete_trailing_window",
            "rolling_regression_line_missing_lookback_is_explicit_and_recovers_when_gap_exits",
        ],
        "handler": "tool_platform.market.technical_indicators.v2_7",
        "operation_graph_id": (
            "tool_platform.market.technical_indicators.v2_7"
        ),
        "input_type": "Stage10TechnicalIndicatorArgumentsV27",
        "input_schema_id": _versioned_schema_id(
            name, "input", version="2.7.0"
        ),
        "input_schema": typed_input_schema(
            "stage10_technical_indicator_v2_7",
            stage10_technical_indicator_input_series_schema(),
        ),
        "output_schema_id": _versioned_schema_id(
            name, "output", version="2.7.0"
        ),
        "output_schema": indicator_output_schema_v27,
        "examples": [
            {
                **copy.deepcopy(indicator_variant_v26["examples"][0]),
                "indicator": "rolling_regression_line",
                "window": 3,
            }
        ],
        "composable": {
            **indicator_variant_v26["composable"],
            "output_types": [
                "QueryResultV1",
                "Stage10TechnicalIndicatorSeriesV27",
            ],
        },
    }
    result.append(
        {
            "tool": name,
            "default_version": "1.0.0",
            "selector_field": "tool_version",
            "variants": [
                indicator_variant,
                indicator_variant_v21,
                indicator_variant_v22,
                indicator_variant_v23,
                indicator_variant_v24,
                indicator_variant_v25,
                indicator_variant_v26,
                indicator_variant_v27,
            ],
            "deprecations": [
                {
                    "version": "1.0.0",
                    "code": "tool_version_deprecated",
                    "message": (
                        "market.technical_indicators version 1.0.0 remains "
                        "available for compatibility; select version 2.0.0 "
                        "for typed store-free OHLCV indicators."
                    ),
                    "replacement": {"tool": name, "version": "2.0.0"},
                    "removal": {"status": "not_scheduled", "milestone": None},
                }
            ],
        }
    )

    examples = {
        "AAPL": stage10_market_return_example("AAPL"),
        "MSFT": stage10_market_return_example("MSFT"),
    }
    analysis_contracts = {
        "timeseries.describe": {
            "input_kind": "stage10_market_describe_v2",
            "input_type": "Stage10MarketDescribeArgumentsV2",
            "description": "Describe one exact Stage 10 close-to-close return series.",
            "example": {"series": examples["AAPL"], "limit": 100},
            "max_series": 1,
            "returns": "matched_stage10_close_to_close_input",
        },
        "timeseries.align": {
            "input_kind": "stage10_market_align_v2",
            "input_type": "Stage10MarketAlignArgumentsV2",
            "description": (
                "Align compatible Stage 10 return series without filling values."
            ),
            "example": {
                "series": [examples["AAPL"], examples["MSFT"]],
                "join": "inner",
                "limit": 100,
            },
            "max_series": 20,
            "returns": "matched_stage10_close_to_close_inputs",
        },
        "timeseries.correlation": {
            "input_kind": "stage10_market_correlation_v2",
            "input_type": "Stage10MarketCorrelationArgumentsV2",
            "description": (
                "Correlate exactly two complete compatible Stage 10 return series."
            ),
            "example": {
                "series": [examples["AAPL"], examples["MSFT"]],
                "limit": 100,
            },
            "max_series": 2,
            "returns": "matched_stage10_close_to_close_complete_pairs",
        },
    }
    for name in (
        item
        for item in VERSIONED_TIMESERIES_ANALYSIS_TOOLS
        if item != "timeseries.transform"
    ):
        contract = analysis_contracts[name]
        operation_graph_id = f"tool_platform.{name}.v2"
        variant = {
            "id": name,
            "family": "timeseries",
            "api_version": "1.0",
            "version": "2.0.0",
            "operation_version": "2.0.0",
            "lifecycle": "experimental",
            "compatibility": {
                "status": "successor_breaking_v2",
                "predecessor": "1.0.0",
            },
            "description": contract["description"],
            "assumptions": [
                "caller_supplied_stage10_return_series",
                "no_store_access",
                "strict_return_contract_compatibility",
                "explicit_missingness",
                "client_snapshot_coherence_not_established",
            ],
            "handler": operation_graph_id,
            "operation_graph_id": operation_graph_id,
            "read_only": True,
            "stores": [],
            "datasets": [],
            "input_type": contract["input_type"],
            "input_schema_id": _versioned_schema_id(name, "input"),
            "input_schema": typed_input_schema(
                contract["input_kind"], analysis_series_schema
            ),
            "output_type": "QueryResultV1",
            "output_schema_id": _versioned_schema_id(name, "output"),
            "output_schema": query_result_schema(name, analysis_series_schema),
            "examples": [contract["example"]],
            "workload_bounds": {
                "max_rows": 10000,
                "max_series": contract["max_series"],
                "max_operations": 5000000,
                "max_request_bytes": 8388608,
                "max_response_bytes": 8388608,
            },
            "cost_model": {
                "expression": "rows + series + operations",
                "deterministic": True,
            },
            "timeout_class": "interactive_5s",
            "availability_policy": {
                "modes": ["inherited_from_typed_input"],
                "point_in_time_default": "inherited_and_revalidated",
            },
            "live_capability": {
                "possible": False,
                "capability_id": None,
                "offline_status": "not_applicable",
            },
            "contracts": {
                "availability": "inherited_from_typed_input",
                "point_in_time": "exact_input_contract",
                "returns": contract["returns"],
            },
            "composable": {
                "input_types": ["Stage10MarketReturnSeriesV2"],
                "output_types": ["QueryResultV1"],
            },
            "observability": "metadata_only",
            "owner": "tool_platform",
            "review_requirements": ["schema", "semantics", "read_only"],
        }
        result.append(
            {
                "tool": name,
                "default_version": "1.0.0",
                "selector_field": "tool_version",
                "variants": [variant],
                "deprecations": [
                    {
                        "version": "1.0.0",
                        "code": "tool_version_deprecated",
                        "message": (
                            f"{name} version 1.0.0 remains available for legacy "
                            "TimeSeries composition; select version 2.0.0 for "
                            "typed Stage 10 return-series composition."
                        ),
                        "replacement": {"tool": name, "version": "2.0.0"},
                        "removal": {"status": "not_scheduled", "milestone": None},
                    }
                ],
            }
        )

    econometric_contracts = {
        "econometrics.regression": {
            "input_kind": "stage10_market_regression_v2",
            "input_type": "Stage10MarketRegressionArgumentsV2",
            "description": (
                "Fit contemporaneous OLS with explicit classical inference "
                "to compatible trailing Stage 10 returns."
            ),
            "example": {
                "series": [examples["AAPL"], examples["MSFT"]],
                "intercept": True,
                "covariance": "classical_homoskedastic",
                "confidence_level": "0.95",
                "limit": 100,
            },
            "max_rows": 5000,
            "max_series": 20,
            "model": "ordinary_least_squares_v2",
            "returns": "matched_stage10_trailing_returns_complete_rows",
        },
        "econometrics.rolling_regression": {
            "input_kind": "stage10_market_rolling_regression_v2",
            "input_type": "Stage10MarketRollingRegressionArgumentsV2",
            "description": (
                "Fit the exact v2 OLS kernel over fixed contiguous windows "
                "of compatible trailing Stage 10 returns."
            ),
            "example": {
                "series": [examples["AAPL"], examples["MSFT"]],
                "window": 3,
                "intercept": True,
                "covariance": "classical_homoskedastic",
                "confidence_level": "0.95",
                "limit": 100,
            },
            "max_rows": 5000,
            "max_series": 20,
            "model": "rolling_ordinary_least_squares_v2",
            "returns": "matched_stage10_trailing_returns_fixed_windows",
        },
        "econometrics.stationarity": {
            "input_kind": "stage10_market_stationarity_v2",
            "input_type": "Stage10MarketStationarityArgumentsV2",
            "description": (
                "Run a fixed-lag constant-only augmented Dickey-Fuller test "
                "with MacKinnon 2010 finite-sample critical values."
            ),
            "example": {
                "series": examples["AAPL"],
                "deterministic": "constant",
                "lag": 0,
                "significance": "0.05",
                "limit": 100,
            },
            "max_rows": 5000,
            "max_series": 1,
            "model": "augmented_dickey_fuller_v2",
            "returns": "one_matched_stage10_trailing_return_series",
        },
        "econometrics.structural_breaks": {
            "input_kind": "stage10_market_structural_breaks_v2",
            "input_type": "Stage10MarketStructuralBreakArgumentsV2",
            "description": (
                "Compare pooled and split classical OLS fits at one "
                "caller-declared coefficient-break index using a Chow F test."
            ),
            "example": {
                "series": [examples["AAPL"], examples["MSFT"]],
                "intercept": True,
                "break_index": 1,
                "significance": "0.05",
                "limit": 100,
            },
            "max_rows": 5000,
            "max_series": 20,
            "model": "chow_pooled_vs_segmented_ols_fixed_break",
            "returns": "matched_stage10_trailing_returns_one_declared_break",
            "assumptions": (
                "caller_declared_zero_based_first_post_break_row",
                "single_break_no_search_or_multiple_testing_adjustment",
                "exact_f_requires_gaussian_homoskedastic_independent_errors_exogenous_fixed_design",
                "each_segment_requires_more_rows_than_fitted_parameters",
                "structural_break_decision_is_in_sample_not_predictive",
            ),
        },

    }
    for name in VERSIONED_ECONOMETRICS_TOOLS:
        contract = econometric_contracts[name]
        operation_graph_id = f"tool_platform.{name}.v2"
        variant = {
            "id": name,
            "family": "econometrics",
            "api_version": "1.0",
            "version": "2.0.0",
            "operation_version": "2.0.0",
            "lifecycle": "experimental",
            "compatibility": {
                "status": "successor_breaking_v2",
                "predecessor": "1.0.0",
            },
            "description": contract["description"],
            "assumptions": [
                "caller_supplied_stage10_trailing_return_series",
                "no_store_access",
                "strict_return_contract_compatibility",
                "explicit_missingness",
                "client_snapshot_coherence_not_established",
                "contemporaneous_association_not_prediction_or_causality",
                *contract.get("assumptions", ()),
            ],
            "handler": operation_graph_id,
            "operation_graph_id": operation_graph_id,
            "read_only": True,
            "stores": [],
            "datasets": [],
            "input_type": contract["input_type"],
            "input_schema_id": _versioned_schema_id(name, "input"),
            "input_schema": typed_input_schema(
                contract["input_kind"], analysis_series_schema
            ),
            "output_type": "QueryResultV1",
            "output_schema_id": _versioned_schema_id(name, "output"),
            "output_schema": query_result_schema(name, analysis_series_schema),
            "examples": [contract["example"]],
            "workload_bounds": {
                "max_rows": contract["max_rows"],
                "max_series": contract["max_series"],
                "max_operations": 5000000,
                "max_request_bytes": 8388608,
                "max_response_bytes": 8388608,
            },
            "cost_model": {
                "expression": "rows + series + operations",
                "deterministic": True,
            },
            "timeout_class": "interactive_5s",
            "availability_policy": {
                "modes": ["inherited_from_typed_input"],
                "point_in_time_default": "inherited_and_revalidated",
            },
            "live_capability": {
                "possible": False,
                "capability_id": None,
                "offline_status": "not_applicable",
            },
            "contracts": {
                "availability": "inherited_from_typed_input",
                "point_in_time": "exact_input_contract",
                "returns": contract["returns"],
                "estimator": contract["model"],
            },
            "composable": {
                "input_types": ["Stage10MarketReturnSeriesV2"],
                "output_types": ["QueryResultV1"],
            },
            "observability": "metadata_only",
            "owner": "tool_platform",
            "review_requirements": ["schema", "semantics", "read_only"],
        }
        variants = [variant]
        if name in {
            "econometrics.regression",
            "econometrics.rolling_regression",
        }:
            rolling = name == "econometrics.rolling_regression"
            version = "2.1.0"
            graph = f"tool_platform.{name}.v2_1"
            example = copy.deepcopy(contract["example"])
            example["covariance"] = "hc1"
            example["hac_lag"] = 0
            example["diagnostic_lag"] = 1
            if rolling:
                example["window"] = 8
            input_kind = (
                "stage10_market_rolling_regression_v2_1"
                if rolling
                else "stage10_market_regression_v2_1"
            )
            input_type = (
                "Stage10MarketRollingRegressionArgumentsV21"
                if rolling
                else "Stage10MarketRegressionArgumentsV21"
            )
            model = (
                "rolling_ordinary_least_squares_robust_inference_v2_1"
                if rolling
                else "ordinary_least_squares_robust_inference_v2_1"
            )
            v21_variant = copy.deepcopy(variant)
            v21_variant.update(
                {
                    "version": version,
                    "operation_version": version,
                    "compatibility": {
                        "status": "successor_explicit_v2_1",
                        "predecessor": "2.0.0",
                    },
                    "description": (
                        "Fit fixed-window OLS with explicit classical, HC1, "
                        "HC3, or fixed-lag Bartlett Newey-West inference and "
                        "per-window residual diagnostics."
                        if rolling
                        else
                        "Fit OLS with explicit classical, HC1, HC3, or "
                        "fixed-lag Bartlett Newey-West inference and fixed-lag "
                        "residual diagnostics."
                    ),
                    "handler": graph,
                    "operation_graph_id": graph,
                    "input_type": input_type,
                    "input_schema_id": _versioned_schema_id(
                        name, "input", version
                    ),
                    "input_schema": typed_input_schema(
                        input_kind, analysis_series_schema
                    ),
                    "output_schema_id": _versioned_schema_id(
                        name, "output", version
                    ),
                    "output_schema": query_result_schema(
                        name, analysis_series_schema
                    ),
                    "examples": [example],
                }
            )
            v21_variant["assumptions"] = [
                *variant["assumptions"],
                "explicit_covariance_estimator",
                "fixed_hac_lag_no_automatic_bandwidth",
                "fixed_residual_diagnostic_lag",
                "robust_inference_uses_asymptotic_normal_reference",
                "residual_diagnostics_use_asymptotic_chi_square_reference",
            ]
            v21_variant["contracts"]["estimator"] = model
            variants.append(v21_variant)
        if name == "econometrics.regression":
            version = "3.0.0"
            graph = "tool_platform.econometrics.regression.v3"
            v3_variant = copy.deepcopy(variant)
            v3_variant.update(
                {
                    "version": version,
                    "operation_version": version,
                    "compatibility": {
                        "status": "successor_breaking_v3",
                        "predecessor": "2.1.0",
                    },
                    "description": (
                        "Run an explicit fixed-specification Engle-Granger "
                        "cointegration, reduced-form VAR, or conditional "
                        "Granger predictive-content analysis."
                    ),
                    "handler": graph,
                    "operation_graph_id": graph,
                    "input_type": (
                        "Stage10MarketRegressionModelSuiteArgumentsV3"
                    ),
                    "input_schema_id": _versioned_schema_id(
                        name, "input", version
                    ),
                    "input_schema": typed_input_schema(
                        "stage10_market_regression_model_suite_v3",
                        analysis_series_schema,
                    ),
                    "output_schema_id": _versioned_schema_id(
                        name, "output", version
                    ),
                    "output_schema": query_result_schema(
                        name, analysis_series_schema
                    ),
                    "examples": [
                        {
                            "series": [examples["AAPL"], examples["MSFT"]],
                            "analysis": "granger_causality",
                            "deterministic": "constant",
                            "lag_order": 1,
                            "significance": "0.05",
                            "source_index": 1,
                            "target_index": 0,
                            "limit": 100,
                        }
                    ],
                }
            )
            v3_variant["assumptions"] = [
                "caller_supplied_stage10_trailing_horizon_one_return_series",
                "no_store_access",
                "strict_return_contract_compatibility",
                "balanced_contiguous_aligned_sample_no_fill_or_row_deletion",
                "fixed_lag_order_no_automatic_selection",
                "constant_deterministic_term_only",
                "engle_granger_assumes_both_log_level_paths_are_i1",
                "engle_granger_is_directional_first_series_on_second",
                "var_is_reduced_form_without_structural_identification",
                "gaussian_homoskedastic_independent_errors_exogenous_fixed_design",
                "granger_means_conditional_predictive_content_not_structural_causality",
                "client_snapshot_coherence_not_established",
                "all_decisions_are_in_sample_not_predictive",
            ]
            v3_variant["contracts"] = {
                "availability": "inherited_from_typed_input",
                "point_in_time": "exact_input_contract",
                "returns": (
                    "balanced_stage10_trailing_horizon_one_returns_or_"
                    "normalized_log_levels_reconstructed_from_them"
                ),
                "estimator": "fixed_specification_econometric_model_suite_v3",
            }
            v3_variant["workload_bounds"]["max_series"] = 5
            variants.append(v3_variant)
        if name == "econometrics.stationarity":
            version = "2.1.0"
            graph = "tool_platform.econometrics.stationarity.v2_1"
            example = copy.deepcopy(contract["example"])
            example["adf_lag"] = example.pop("lag")
            example["kpss_lag"] = 2
            v21_variant = copy.deepcopy(variant)
            v21_variant.update(
                {
                    "version": version,
                    "operation_version": version,
                    "compatibility": {
                        "status": "successor_explicit_v2_1",
                        "predecessor": "2.0.0",
                    },
                    "description": (
                        "Run fixed-lag constant-only ADF and level-KPSS tests "
                        "with one explicit joint interpretation."
                    ),
                    "handler": graph,
                    "operation_graph_id": graph,
                    "input_type": "Stage10MarketStationarityArgumentsV21",
                    "input_schema_id": _versioned_schema_id(
                        name, "input", version
                    ),
                    "input_schema": typed_input_schema(
                        "stage10_market_stationarity_v2_1",
                        analysis_series_schema,
                    ),
                    "output_schema_id": _versioned_schema_id(
                        name, "output", version
                    ),
                    "output_schema": query_result_schema(
                        name, analysis_series_schema
                    ),
                    "examples": [example],
                }
            )
            v21_variant["assumptions"] = [
                *variant["assumptions"],
                "fixed_adf_lag_no_automatic_selection",
                "fixed_kpss_bartlett_lag_no_automatic_bandwidth",
                "kpss_level_stationarity_null",
                "joint_interpretation_uses_one_fixed_significance_level",
                "stationarity_decisions_are_in_sample_not_predictive",
            ]
            v21_variant["contracts"]["estimator"] = (
                "adf_fixed_lag_plus_kpss_level_fixed_bartlett_lag_v2_1"
            )
            variants.append(v21_variant)


        result.append(
            {
                "tool": name,
                "default_version": "1.0.0",
                "selector_field": "tool_version",
                "variants": variants,
                "deprecations": [
                    {
                        "version": "1.0.0",
                        "code": "tool_version_deprecated",
                        "message": (
                            f"{name} version 1.0.0 remains available for legacy "
                            "TimeSeries composition; select version 2.0.0 for "
                            "typed Stage 10 econometrics."
                        ),
                        "replacement": {"tool": name, "version": "2.0.0"},
                        "removal": {"status": "not_scheduled", "milestone": None},
                    }
                ],
            }
        )
    result.extend(
        _build_quality_transform_policies(
            analysis_series_schema=analysis_series_schema,
            examples=examples,
        )
    )
    result.extend(
        _build_research_analytic_policies(
            analysis_series_schema=analysis_series_schema,
            examples=examples,
        )
    )
    result.extend(
        _build_company_filing_policies(
            analysis_series_schema=analysis_series_schema,
        )
    )
    result.extend(
        _build_company_share_count_policies(
            analysis_series_schema=analysis_series_schema,
        )
    )
    result.extend(
        _build_market_instrument_search_policies(
            analysis_series_schema=analysis_series_schema,
        )
    )
    result.extend(
        _build_news_search_policies(
            analysis_series_schema=analysis_series_schema,
        )
    )
    result.extend(
        _build_investment_analysis_policies(
            analysis_series_schema=analysis_series_schema,
        )
    )
    return tuple(result)


def _build_quality_transform_policies(
    *,
    analysis_series_schema: Mapping[str, Any],
    examples: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Build explicit v2 successors for recovered placeholder analytics."""

    contracts = (
        {
            "name": "data.quality_audit",
            "input_kind": "stage10_data_quality_v2",
            "input_type": "Stage10DataQualityArgumentsV2",
            "description": (
                "Audit coverage, explicit gaps and missingness, duplicates, "
                "availability timing, and lineage for supplied Stage 10 returns."
            ),
            "assumptions": (
                "calendar_discontinuities_are_not_claimed_as_missing_trading_sessions",
                "truncated_input_is_reported_and_coverage_is_not_established",
                "duplicate_and_out_of_order_periods_are_reported_not_repaired",
                "no_raw_evidence_payloads_are_returned",
            ),
            "example": {
                "series": [examples["AAPL"], examples["MSFT"]],
                "limit": 100,
            },
            "max_series": 20,
            "estimator": "stage10_data_quality_diagnostics_v2",
        },
        {
            "name": "timeseries.transform",
            "input_kind": "stage10_market_transform_v2",
            "input_type": "Stage10MarketTransformArgumentsV2",
            "description": (
                "Compute rolling statistics, ACF, PACF, Ljung-Box diagnostics, "
                "or drawdown episodes over one Stage 10 trailing-return series."
            ),
            "assumptions": (
                "operation_specific_parameters_are_explicit",
                "rolling_windows_are_fixed_and_never_shrunk",
                "acf_uses_full_sample_centered_denominator",
                "pacf_uses_durbin_levinson_recursion",
                "ljung_box_uses_fixed_lag_and_zero_model_degrees_of_freedom",
                "drawdown_wealth_starts_at_one",
                "terminal_missingness_may_be_trimmed_but_interior_gaps_are_not_filled",
            ),
            "example": {
                "series": examples["AAPL"],
                "operation": "rolling_statistic",
                "rolling_statistic": "mean",
                "window": 2,
                "max_lag": None,
                "ljung_box_lag": None,
                "limit": 100,
            },
            "max_series": 1,
            "estimator": "stage10_time_series_transform_suite_v2",
        },
    )
    policies: list[dict[str, Any]] = []
    for contract in contracts:
        name = str(contract["name"])
        graph = f"tool_platform.{name}.v2"
        output_schema = query_result_schema(name, analysis_series_schema)
        output_schema["properties"]["series"]["maxItems"] = 0
        variant = {
            "id": name,
            "family": _family(name),
            "api_version": "1.0",
            "version": "2.0.0",
            "operation_version": "2.0.0",
            "lifecycle": "experimental",
            "compatibility": {
                "status": "successor_breaking_v2",
                "predecessor": "1.0.0",
            },
            "description": contract["description"],
            "assumptions": [
                "caller_supplied_stage10_trailing_return_series",
                "no_store_access",
                "explicit_missingness_no_fill",
                "point_in_time_contract_inherited_and_revalidated",
                *contract["assumptions"],
            ],
            "handler": graph,
            "operation_graph_id": graph,
            "read_only": True,
            "stores": [],
            "datasets": [],
            "input_type": contract["input_type"],
            "input_schema_id": _versioned_schema_id(name, "input"),
            "input_schema": typed_input_schema(
                contract["input_kind"], analysis_series_schema
            ),
            "output_type": "QueryResultV1",
            "output_schema_id": _versioned_schema_id(name, "output"),
            "output_schema": output_schema,
            "examples": [contract["example"]],
            "workload_bounds": {
                "max_rows": 10000,
                "max_series": contract["max_series"],
                "max_operations": 5000000,
                "max_request_bytes": 8388608,
                "max_response_bytes": 8388608,
            },
            "cost_model": {
                "expression": "rows + series + operations",
                "deterministic": True,
            },
            "timeout_class": "interactive_5s",
            "availability_policy": {
                "modes": ["inherited_from_typed_input"],
                "point_in_time_default": "inherited_and_revalidated",
            },
            "live_capability": {
                "possible": False,
                "capability_id": None,
                "offline_status": "not_applicable",
            },
            "contracts": {
                "availability": "inherited_from_typed_input",
                "point_in_time": "exact_input_contract",
                "returns": "stage10_return_series",
                "estimator": contract["estimator"],
            },
            "composable": {
                "input_types": ["Stage10MarketReturnSeriesV2"],
                "output_types": ["QueryResultV1"],
            },
            "observability": "metadata_only",
            "owner": "tool_platform",
            "review_requirements": ["schema", "semantics", "read_only"],
        }
        policies.append(
            {
                "tool": name,
                "default_version": "1.0.0",
                "selector_field": "tool_version",
                "variants": [variant],
                "deprecations": [
                    {
                        "version": "1.0.0",
                        "code": "tool_version_deprecated",
                        "message": (
                            f"{name} version 1.0.0 remains available for legacy "
                            "composition; select version 2.0.0 for typed Stage 10 "
                            "analytics."
                        ),
                        "replacement": {"tool": name, "version": "2.0.0"},
                        "removal": {"status": "not_scheduled", "milestone": None},
                    }
                ],
            }
        )
    return tuple(policies)


def _build_research_analytic_policies(
    *,
    analysis_series_schema: Mapping[str, Any],
    examples: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Bind the remaining recovered analytical kernels to Stage 10 inputs."""

    contracts = (
        (
            "research.point_in_time_panel",
            "Align supplied Stage 10 returns without filling missing observations.",
            ("AAPL", "MSFT"),
            ({"name": "join", "value": "inner"},),
            20,
        ),
        (
            "research.event_study",
            "Summarize one caller-indexed event window over Stage 10 returns.",
            ("AAPL",),
            (
                {"name": "event_index", "value": 1},
                {"name": "pre", "value": 0},
                {"name": "post", "value": 0},
            ),
            1,
        ),
        (
            "alpha.signal_diagnostics",
            "Describe one return series or compare an explicit signal/outcome pair.",
            ("AAPL", "MSFT"),
            (),
            2,
        ),
        (
            "research.walk_forward_backtest",
            "Evaluate aligned caller-supplied realized and predicted returns.",
            ("AAPL", "MSFT"),
            (),
            2,
        ),
        (
            "research.robustness_suite",
            "Report deterministic descriptive diagnostics for one return series.",
            ("AAPL",),
            (),
            1,
        ),
        (
            "stats.multiple_testing",
            "Apply Benjamini-Hochberg when supplied values are valid p-values.",
            ("AAPL",),
            (),
            1,
        ),
        (
            "forecast.evaluate",
            "Evaluate aligned caller-supplied realized and forecast returns.",
            ("AAPL", "MSFT"),
            (),
            2,
        ),
    )
    policies: list[dict[str, Any]] = []
    for name, description, symbols, parameters, max_series in contracts:
        graph = f"tool_platform.{name}.v2"
        output_schema = query_result_schema(name, analysis_series_schema)
        output_schema["properties"]["series"]["maxItems"] = 0
        variant = {
            "id": name,
            "family": _family(name),
            "api_version": "1.0",
            "version": "2.0.0",
            "operation_version": "2.0.0",
            "lifecycle": "experimental",
            "compatibility": {
                "status": "successor_breaking_v2",
                "predecessor": "1.0.0",
            },
            "description": description,
            "assumptions": [
                "caller_supplied_stage10_trailing_return_series",
                "no_store_access",
                "explicit_missingness_no_fill",
                "point_in_time_contract_inherited_and_revalidated",
                "tool_specific_value_domain_is_enforced",
            ],
            "handler": graph,
            "operation_graph_id": graph,
            "read_only": True,
            "stores": [],
            "datasets": [],
            "input_type": "ResearchSeriesArguments",
            "input_schema_id": _versioned_schema_id(name, "input"),
            "input_schema": typed_input_schema("research", analysis_series_schema),
            "output_type": "QueryResultV1",
            "output_schema_id": _versioned_schema_id(name, "output"),
            "output_schema": output_schema,
            "examples": [
                {
                    "series": [
                        copy.deepcopy(examples[symbol]) for symbol in symbols
                    ],
                    "parameters": copy.deepcopy(list(parameters)),
                    "limit": 100,
                    "as_of": None,
                    "unsafe_ok": True,
                }
            ],
            "workload_bounds": {
                "max_rows": 10000,
                "max_series": max_series,
                "max_operations": 5000000,
                "max_request_bytes": 8388608,
                "max_response_bytes": 8388608,
            },
            "cost_model": {
                "expression": "rows + series + operations",
                "deterministic": True,
            },
            "timeout_class": "interactive_5s",
            "availability_policy": {
                "modes": ["inherited_from_typed_input"],
                "point_in_time_default": "inherited_and_revalidated",
            },
            "live_capability": {
                "possible": False,
                "capability_id": None,
                "offline_status": "not_applicable",
            },
            "contracts": {
                "availability": "inherited_from_typed_input",
                "point_in_time": "exact_input_contract",
                "returns": "stage10_return_series",
                "estimator": "recovered_deterministic_analytic_kernel_v1",
            },
            "composable": {
                "input_types": ["Stage10MarketReturnSeriesV2"],
                "output_types": ["QueryResultV1"],
            },
            "observability": "metadata_only",
            "owner": "tool_platform",
            "review_requirements": ["schema", "semantics", "read_only"],
        }
        policies.append(
            {
                "tool": name,
                "default_version": "1.0.0",
                "selector_field": "tool_version",
                "variants": [variant],
                "deprecations": [
                    {
                        "version": "1.0.0",
                        "code": "tool_version_deprecated",
                        "message": (
                            f"{name} version 1.0.0 remains available for legacy "
                            "composition; select version 2.0.0 for typed Stage 10 "
                            "composition."
                        ),
                        "replacement": {"tool": name, "version": "2.0.0"},
                        "removal": {"status": "not_scheduled", "milestone": None},
                    }
                ],
            }
        )
    return tuple(policies)


def _build_news_search_policies(
    *,
    analysis_series_schema: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    """Build the retained current-news search successor."""

    name = "news.search"
    graph = "tool_platform.news.search.v2"
    output_schema = query_result_schema(name, analysis_series_schema)
    output_schema["properties"]["series"]["maxItems"] = 0
    variant = {
        "id": name,
        "family": "research",
        "api_version": "1.0",
        "version": "2.0.0",
        "operation_version": "2.0.0",
        "lifecycle": "experimental",
        "compatibility": {
            "status": "successor_breaking_v2",
            "predecessor": "1.0.0",
        },
        "description": (
            "Search retained FMP current-news headline metadata with explicit "
            "latest or local-capture as-of selection."
        ),
        "assumptions": [
            "host_selected_news_store",
            "one_partial_provider_page_per_capture",
            "partial_pages_do_not_imply_article_tombstones",
            "publication_precision_is_source_native",
            "date_only_publication_offsets_are_not_invented",
            "article_bodies_and_raw_provider_bytes_remain_private",
        ],
        "handler": graph,
        "operation_graph_id": graph,
        "read_only": True,
        "stores": ["news"],
        "datasets": [
            "news.fmp.stock_latest_current_evidence",
            "news.fmp.stock_latest_current_articles",
        ],
        "input_type": "CurrentNewsSearchArgumentsV2",
        "input_schema_id": _versioned_schema_id(name, "input"),
        "input_schema": typed_input_schema("current_news_search_v2", {}),
        "output_type": "QueryResultV1",
        "output_schema_id": _versioned_schema_id(name, "output"),
        "output_schema": output_schema,
        "examples": [
            {
                "query": "",
                "symbols": ["AAPL"],
                "mode": "latest",
                "as_of": None,
                "date_only_policy": "completed_date",
                "start_date": None,
                "end_date": None,
                "limit": 100,
            }
        ],
        "workload_bounds": {
            "max_rows": 500,
            "max_series": 1,
            "max_operations": 200000,
            "max_request_bytes": 1048576,
            "max_response_bytes": 8388608,
        },
        "cost_model": {
            "expression": "candidate_rows + returned_rows",
            "deterministic": True,
        },
        "timeout_class": "interactive_5s",
        "availability_policy": {
            "modes": ["latest", "as_of"],
            "default_date_only_policy": "completed_date",
            "point_in_time_default": "latest",
            "as_of_point_in_time_status": "safe_for_retained_local_captures",
            "date_bounds": "optional_inclusive_source_publication_date",
        },
        "live_capability": {
            "possible": False,
            "capability_id": None,
            "offline_status": "not_applicable",
        },
        "contracts": {
            "availability": "local_capture",
            "point_in_time": "exact_optional_capture_cutoff",
            "returns": "not_applicable",
            "coverage": "one_retained_partial_provider_page_per_capture",
        },
        "composable": {
            "input_types": [],
            "output_types": ["QueryResultV1"],
        },
        "observability": "metadata_only",
        "owner": "news",
        "review_requirements": ["schema", "semantics", "read_only"],
    }
    graph_v21 = "tool_platform.news.search.v2_1"
    variant_v21 = copy.deepcopy(variant)
    variant_v21.update(
        {
            "version": "2.1.0",
            "operation_version": "2.1.0",
            "compatibility": {
                "status": "successor_additive_v2_1",
                "predecessor": "2.0.0",
            },
            "description": (
                "Search retained FMP, official-source, and Alpaca/Benzinga "
                "headline metadata with optional source filtering."
            ),
            "handler": graph_v21,
            "operation_graph_id": graph_v21,
            "datasets": [
                *variant["datasets"],
                "news.current_multi_source_evidence",
                "news.current_multi_source_articles",
            ],
            "input_type": "CurrentNewsSearchArgumentsV21",
            "input_schema_id": _versioned_schema_id(
                name, "input", "2.1.0"
            ),
            "input_schema": typed_input_schema(
                "current_news_search_v2_1", {}
            ),
            "output_schema_id": _versioned_schema_id(
                name, "output", "2.1.0"
            ),
            "examples": [
                {**variant["examples"][0], "source_ids": []}
            ],
        }
    )
    variant_v21["assumptions"] = [
        *variant["assumptions"],
        "source_identity_is_scoped_to_one_fixed_feed",
        "cross_feed_deduplication_is_not_claimed",
        "official_feed_items_may_have_no_ticker_association",
    ]
    variant_v21["contracts"] = {
        **variant["contracts"],
        "coverage": "retained_partial_captures_across_fixed_current_sources",
    }
    return (
        {
            "tool": name,
            "default_version": "1.0.0",
            "selector_field": "tool_version",
            "variants": [variant, variant_v21],
            "deprecations": [
                {
                    "version": "1.0.0",
                    "code": "tool_version_deprecated",
                    "message": (
                        "news.search version 1.0.0 remains available for the "
                        "frozen Stage 4 fixture; select version 2.0.0 for "
                        "retained current FMP headline metadata."
                    ),
                    "replacement": {"tool": name, "version": "2.0.0"},
                    "removal": {
                        "status": "not_scheduled",
                        "milestone": None,
                    },
                }
            ],
        },
    )


def _build_company_filing_policies(
    *,
    analysis_series_schema: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    """Build the explicit keyset-paginated filing-search successor."""

    name = "company.search_filings"
    graph = "tool_platform.company.search_filings.v2"
    output_schema = query_result_schema(name, analysis_series_schema)
    output_schema["properties"]["series"]["maxItems"] = 0
    variant = {
        "id": name,
        "family": "company",
        "api_version": "1.0",
        "version": "2.0.0",
        "operation_version": "2.0.0",
        "lifecycle": "experimental",
        "compatibility": {
            "status": "successor_breaking_v2",
            "predecessor": "1.0.0",
        },
        "description": (
            "Page deterministically through one exact CIK filing history."
        ),
        "assumptions": [
            "query_is_one_exact_ten_digit_sec_cik",
            "ascending_filing_date_then_accession_order",
            "cursor_is_opaque_and_bound_to_query_and_cutoff",
            "total_known_count_is_not_computed",
        ],
        "handler": graph,
        "operation_graph_id": graph,
        "read_only": True,
        "stores": ["company"],
        "datasets": ["fixture.company.filings"],
        "input_type": "CompanyFilingSearchArgumentsV2",
        "input_schema_id": _versioned_schema_id(name, "input"),
        "input_schema": typed_input_schema(
            "company_filing_search_v2",
            {},
        ),
        "output_type": "QueryResultV1",
        "output_schema_id": _versioned_schema_id(name, "output"),
        "output_schema": output_schema,
        "examples": [
            {
                "query": "0000320193",
                "as_of": None,
                "cursor": None,
                "limit": 100,
            }
        ],
        "workload_bounds": {
            "max_rows": 500,
            "max_series": 1,
            "max_operations": 200000,
            "max_request_bytes": 1048576,
            "max_response_bytes": 8388608,
        },
        "cost_model": {
            "expression": "candidate_rows + returned_rows",
            "deterministic": True,
        },
        "timeout_class": "interactive_5s",
        "availability_policy": {
            "modes": ["latest", "as_of"],
            "point_in_time_default": "latest",
        },
        "live_capability": {
            "possible": False,
            "capability_id": None,
            "offline_status": "not_applicable",
        },
        "contracts": {
            "availability": "source_available_at_with_optional_as_of_cutoff",
            "point_in_time": "exact_optional_cutoff",
            "returns": "not_applicable",
            "pagination": "keyset_filing_date_accession_v1",
        },
        "composable": {
            "input_types": [],
            "output_types": ["QueryResultV1"],
        },
        "observability": "metadata_only",
        "owner": "tool_platform",
        "review_requirements": ["schema", "semantics", "read_only"],
    }
    return (
        {
            "tool": name,
            "default_version": "1.0.0",
            "selector_field": "tool_version",
            "variants": [variant],
            "deprecations": [
                {
                    "version": "1.0.0",
                    "code": "tool_version_deprecated",
                    "message": (
                        "company.search_filings version 1.0.0 remains "
                        "available for legacy bounded search; select version "
                        "2.0.0 for deterministic cursor pagination."
                    ),
                    "replacement": {
                        "tool": name,
                        "version": "2.0.0",
                    },
                    "removal": {
                        "status": "not_scheduled",
                        "milestone": None,
                    },
                }
            ],
        },
    )



def _build_company_share_count_policies(
    *,
    analysis_series_schema: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    """Build the reviewed SEC share-count history successor."""

    name = "company.get_share_count_history"
    graph = "tool_platform.company.get_share_count_history.v2"
    output_schema = query_result_schema(name, analysis_series_schema)
    output_schema["properties"]["series"]["maxItems"] = 0
    variant = {
        "id": name,
        "family": "company",
        "api_version": "1.0",
        "version": "2.0.0",
        "operation_version": "2.0.0",
        "lifecycle": "experimental",
        "compatibility": {
            "status": "successor_breaking_v2",
            "predecessor": "1.0.0",
        },
        "description": (
            "Read the reviewed SEC outstanding, basic weighted-average, and "
            "diluted weighted-average share-count facts for one exact CIK."
        ),
        "assumptions": [
            "query_is_one_exact_ten_digit_sec_cik",
            "metric_family_is_fixed_by_reviewed_sec_mappings",
            "instant_and_weighted_average_share_semantics_remain_distinct",
            "no_split_adjustment_or_missing_metric_inference_is_applied",
            "ascending_period_end_then_metric_then_source_fact_order",
        ],
        "handler": graph,
        "operation_graph_id": graph,
        "read_only": True,
        "stores": ["company"],
        "datasets": ["fixture.company.fundamentals"],
        "input_type": "CompanyShareCountHistoryArgumentsV2",
        "input_schema_id": _versioned_schema_id(name, "input"),
        "input_schema": typed_input_schema(
            "company_share_count_history_v2",
            {},
        ),
        "output_type": "QueryResultV1",
        "output_schema_id": _versioned_schema_id(name, "output"),
        "output_schema": output_schema,
        "examples": [
            {
                "cik": "0000320193",
                "mode": "latest",
                "as_of": None,
                "date_only_policy": "completed_date",
                "limit": 1000,
            }
        ],
        "workload_bounds": {
            "max_rows": 10000,
            "max_series": 1,
            "max_operations": 200000,
            "max_request_bytes": 1048576,
            "max_response_bytes": 8388608,
        },
        "cost_model": {
            "expression": "candidate_rows + returned_rows",
            "deterministic": True,
        },
        "timeout_class": "interactive_5s",
        "availability_policy": {
            "modes": ["latest", "as_of"],
            "point_in_time_default": "latest",
        },
        "live_capability": {
            "possible": False,
            "capability_id": None,
            "offline_status": "not_applicable",
        },
        "contracts": {
            "availability": "source_available_at_with_optional_as_of_cutoff",
            "point_in_time": "exact_optional_cutoff",
            "returns": "not_applicable",
            "pagination": "not_applicable_bounded_result",
        },
        "composable": {
            "input_types": [],
            "output_types": ["QueryResultV1"],
        },
        "observability": "metadata_only",
        "owner": "tool_platform",
        "review_requirements": ["schema", "semantics", "read_only"],
    }
    return (
        {
            "tool": name,
            "default_version": "1.0.0",
            "selector_field": "tool_version",
            "variants": [variant],
            "deprecations": [
                {
                    "version": "1.0.0",
                    "code": "tool_version_deprecated",
                    "message": (
                        "company.get_share_count_history version 1.0.0 remains "
                        "available as an honest compatibility placeholder; "
                        "select version 2.0.0 for reviewed SEC share facts."
                    ),
                    "replacement": {"tool": name, "version": "2.0.0"},
                    "removal": {
                        "status": "not_scheduled",
                        "milestone": None,
                    },
                }
            ],
        },
    )


def _build_market_instrument_search_policies(
    *,
    analysis_series_schema: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    """Build the bounded Stage 10 retained-universe search successor."""

    name = "market.search_instruments"
    graph = "tool_platform.market.search_instruments.v2"
    output_schema = query_result_schema(name, analysis_series_schema)
    output_schema["properties"]["series"]["maxItems"] = 0
    variant = {
        "id": name,
        "family": "market",
        "api_version": "1.0",
        "version": "2.0.0",
        "operation_version": "2.0.0",
        "lifecycle": "experimental",
        "compatibility": {
            "status": "successor_breaking_v2",
            "predecessor": "1.0.0",
        },
        "description": (
            "Search the retained Stage 10 FMP instrument universe with "
            "deterministic cursor pagination."
        ),
        "assumptions": [
            "query_is_a_literal_substring_of_provider_symbol_or_display_name",
            "empty_query_lists_the_retained_universe",
            "optional_asset_type_filter_is_exact",
            "ascending_provider_symbol_then_instrument_id_order",
            "cursor_is_opaque_and_bound_to_query_and_asset_type",
            "instrument_identity_does_not_imply_price_coverage",
        ],
        "handler": graph,
        "operation_graph_id": graph,
        "read_only": True,
        "stores": ["market"],
        "datasets": ["market.stage10.instruments"],
        "input_type": "Stage10MarketInstrumentSearchArgumentsV2",
        "input_schema_id": _versioned_schema_id(name, "input"),
        "input_schema": typed_input_schema(
            "stage10_market_instrument_search_v2",
            {},
        ),
        "output_type": "QueryResultV1",
        "output_schema_id": _versioned_schema_id(name, "output"),
        "output_schema": output_schema,
        "examples": [
            {
                "query": "SP",
                "asset_type": None,
                "cursor": None,
                "limit": 100,
            }
        ],
        "workload_bounds": {
            "max_rows": 100,
            "max_series": 1,
            "max_operations": 200000,
            "max_request_bytes": 1048576,
            "max_response_bytes": 8388608,
        },
        "cost_model": {
            "expression": "candidate_rows + returned_rows",
            "deterministic": True,
        },
        "timeout_class": "interactive_5s",
        "availability_policy": {
            "modes": ["latest"],
            "point_in_time_default": "latest",
        },
        "live_capability": {
            "possible": False,
            "capability_id": None,
            "offline_status": "not_applicable",
        },
        "contracts": {
            "availability": "retained_current_universe",
            "point_in_time": "not_established",
            "returns": "not_applicable",
            "pagination": "keyset_provider_symbol_instrument_id_v1",
        },
        "composable": {
            "input_types": [],
            "output_types": ["QueryResultV1"],
        },
        "observability": "metadata_only",
        "owner": "tool_platform",
        "review_requirements": ["schema", "semantics", "read_only"],
    }
    return (
        {
            "tool": name,
            "default_version": "1.0.0",
            "selector_field": "tool_version",
            "variants": [variant],
            "deprecations": [
                {
                    "version": "1.0.0",
                    "code": "tool_version_deprecated",
                    "message": (
                        "market.search_instruments version 1.0.0 remains "
                        "available as an honest compatibility placeholder; "
                        "select version 2.0.0 for retained-universe search."
                    ),
                    "replacement": {"tool": name, "version": "2.0.0"},
                    "removal": {
                        "status": "not_scheduled",
                        "milestone": None,
                    },
                }
            ],
        },
    )

def _build_investment_analysis_policies(
    *,
    analysis_series_schema: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    """Build the bounded raw-data and descriptive-analysis v2 successors."""

    calendar = (
        "macro.fmp.economic_calendar_evidence",
        "macro.fmp.economic_calendar_incremental_evidence",
        "macro.fmp.economic_calendar_incremental_events",
    )
    generic = (
        "fixture.macro.rtdsm_employ",
        "fixture.macro.rtdsm_employ_evidence",
        "fixture.macro.stage3_catalog",
    )
    official = (
        "macro.official_vintages",
        "macro.official_vintages_evidence",
    )
    canonical = (*generic, *official)
    surprise_sources = ("fixture.macro.economic_calendar", *official)
    soma = (
        "fixture.macro.soma_summary",
        "fixture.macro.soma_evidence",
    )
    conditions = (*canonical, *soma)
    temporal = {
        "modes": ["latest", "as_of"],
        "default_date_only_policy": "completed_date",
        "point_in_time_default": "latest",
        "as_of_point_in_time_status": "safe_for_stored_availability",
    }
    snapshot = {
        "mode": "latest",
        "as_of": None,
        "date_only_policy": "completed_date",
        "observation_date": None,
        "limit": 20,
    }
    specs = (
        {
            "name": "macro.get_release_calendar",
            "kind": "macro_release_calendar_v2",
            "type": "MacroReleaseCalendarArgumentsV2",
            "description": (
                "Page through the retained release calendar with an opaque "
                "cursor bound to the complete query."
            ),
            "assumptions": (
                "keyset_order_is_event_at_event_name_event_id",
                "cursor_is_opaque_and_query_bound",
                "calendar_values_remain_source_native",
            ),
            "stores": ("macro",),
            "datasets": calendar,
            "example": {
                "mode": "latest",
                "as_of": None,
                "date_only_policy": "completed_date",
                "limit": 100,
                "start_date": None,
                "end_date": None,
                "event_name": None,
                "cursor": None,
            },
            "max_rows": 10_000,
            "estimator": "query_bound_keyset_calendar_v2",
        },
        {
            "name": "market.cross_sectional_performance",
            "kind": "stage10_cross_sectional_performance_v2",
            "type": "Stage10CrossSectionalPerformanceArgumentsV2",
            "description": (
                "Rank endpoint simple returns for an explicit bounded set of "
                "Stage 10 symbols and report every symbol's coverage."
            ),
            "assumptions": (
                "explicit_symbols_only_no_universe_inference",
                "close_to_close_endpoint_simple_return",
                "no_portfolio_weights_or_position_semantics",
                "incomplete_series_are_excluded_from_ranking",
            ),
            "stores": ("market",),
            "datasets": (
                "market.stage10.daily_prices",
                "market.stage10.source_evidence",
                "market.stage10.instruments",
            ),
            "example": {
                "tickers": ["AAPL", "MSFT"],
                "mode": "latest",
                "as_of": None,
                "date_only_policy": "completed_date",
                "limit": 100,
                "start_date": None,
                "end_date": None,
            },
            "max_rows": 10_000,
            "estimator": "endpoint_simple_return_rank_percentile_v2",
        },
        {
            "name": "macro.revision_analysis",
            "kind": "macro_revision_v2",
            "type": "MacroRevisionArgumentsV2",
            "description": (
                "Compare evidenced first-release and current retained latest "
                "values for one reviewed official-vintage series."
            ),
            "assumptions": (
                "current_retained_first_vs_latest_not_historical_as_of",
                "only_reviewed_official_vintage_series",
            ),
            "stores": ("macro",),
            "datasets": canonical,
            "example": {
                "series_id": "macro.gdp.real_qoq_saar_pct",
                "start_date": None,
                "end_date": None,
                "limit": 500,
            },
            "max_rows": 10_000,
            "availability": {
                "modes": ["current_retained_first_vs_latest"],
                "point_in_time_default": "not_applicable",
            },
            "estimator": "signed_and_absolute_first_vs_latest_revision_v2",
        },
        {
            "name": "macro.standardize_surprises",
            "kind": "macro_surprise_standardization_v2",
            "type": "MacroSurpriseStandardizationArgumentsV2",
            "description": (
                "Standardize one reviewed retained macro surprise kind using "
                "its ex-post population mean and standard deviation."
            ),
            "assumptions": (
                "population_standard_deviation",
                "latest_retained_ex_post_population",
                "at_least_two_same_unit_observations",
            ),
            "stores": ("macro",),
            "datasets": surprise_sources,
            "example": {
                "kind": "us_cpi_headline_mom",
                "release_stage": None,
                "start_date": None,
                "end_date": None,
                "limit": 500,
            },
            "max_rows": 2_000,
            "availability": {
                "modes": ["latest_retained_ex_post"],
                "point_in_time_default": "not_applicable",
            },
            "estimator": "population_z_score_by_unit_v2",
        },
        {
            "name": "rates.get_funding_conditions",
            "kind": "funding_conditions_v2",
            "type": "FundingConditionsArgumentsV2",
            "description": (
                "Return observed funding rates and one optional direct "
                "same-date, same-unit spread."
            ),
            "assumptions": (
                "raw_observed_components",
                "optional_spread_is_left_minus_right",
                "no_funding_score_or_regime_classification",
            ),
            "stores": ("macro",),
            "datasets": canonical,
            "example": {
                **snapshot,
                "spread_left": "EFFR",
                "spread_right": "SOFR",
            },
            "estimator": "raw_funding_vector_optional_direct_spread_v2",
        },
        {
            "name": "rates.get_repo_facility_usage",
            "kind": "macro_observed_snapshot_v2",
            "type": "MacroObservedSnapshotArgumentsV2",
            "description": "Return retained observed ON RRP and SRF usage facts.",
            "assumptions": (
                "raw_observed_components",
                "no_balance_sheet_or_policy_inference",
            ),
            "stores": ("macro",),
            "datasets": canonical,
            "example": snapshot,
            "estimator": "raw_repo_facility_vector_v2",
        },
        {
            "name": "rates.curve_analytics",
            "kind": "curve_analytics_v2",
            "type": "CurveAnalyticsArgumentsV2",
            "description": (
                "Return observed Treasury par-yield tenors and one optional "
                "direct same-date tenor spread."
            ),
            "assumptions": (
                "raw_observed_tenors",
                "optional_spread_is_left_minus_right",
                "no_interpolation_or_curve_model",
            ),
            "stores": ("macro",),
            "datasets": canonical,
            "example": {
                **snapshot,
                "spread_left_tenor": "10Y",
                "spread_right_tenor": "2Y",
            },
            "estimator": "raw_curve_vector_optional_direct_spread_v2",
        },
        {
            "name": "macro.get_liquidity_snapshot",
            "kind": "macro_observed_snapshot_v2",
            "type": "MacroObservedSnapshotArgumentsV2",
            "description": (
                "Return raw Federal Reserve, reserve-balance, TGA, and SOMA "
                "liquidity components."
            ),
            "assumptions": (
                "raw_observed_components",
                "source_units_are_not_forced_into_one_aggregate",
                "no_liquidity_score",
            ),
            "stores": ("macro",),
            "datasets": conditions,
            "example": snapshot,
            "estimator": "raw_liquidity_component_vector_v2",
        },
        {
            "name": "macro.get_liquidity_impulse",
            "kind": "liquidity_impulse_v2",
            "type": "LiquidityImpulseArgumentsV2",
            "description": (
                "Return end-minus-start changes for each comparable retained "
                "liquidity component without aggregating units."
            ),
            "assumptions": (
                "component_end_minus_start",
                "same_component_same_unit_pairs_only",
                "no_cross_component_aggregate",
            ),
            "stores": ("macro",),
            "datasets": conditions,
            "example": {
                "mode": "latest",
                "as_of": None,
                "date_only_policy": "completed_date",
                "start_date": "2026-01-01",
                "end_date": "2026-08-01",
                "limit": 20,
            },
            "estimator": "component_end_minus_start_delta_v2",
        },
        {
            "name": "macro.get_credit_conditions",
            "kind": "macro_observed_snapshot_v2",
            "type": "MacroObservedSnapshotArgumentsV2",
            "description": (
                "Return raw NFCI, ANFCI, BIS credit, and CMDI components "
                "without collapsing them into a score."
            ),
            "assumptions": (
                "raw_observed_components",
                "source_units_are_preserved",
                "no_credit_score_or_classification",
            ),
            "stores": ("macro",),
            "datasets": canonical,
            "example": snapshot,
            "estimator": "raw_credit_component_vector_v2",
        },
        {
            "name": "macro.regime_snapshot",
            "kind": "macro_regime_v2",
            "type": "MacroRegimeArgumentsV2",
            "description": (
                "Return the direct retained NBER expansion or recession "
                "indicator with optional raw context."
            ),
            "assumptions": (
                "regime_is_direct_stored_nber_indicator",
                "optional_context_remains_unscored",
                "no_model_based_regime_inference",
            ),
            "stores": ("macro",),
            "datasets": conditions,
            "example": {**snapshot, "include_context": False},
            "estimator": "direct_nber_indicator_v2",
        },
        {
            "name": "research.liquidity_credit_state",
            "kind": "macro_regime_v2",
            "type": "MacroRegimeArgumentsV2",
            "description": (
                "Return raw liquidity and credit components in a research "
                "envelope without a score or state label."
            ),
            "assumptions": (
                "descriptive_raw_vector_only",
                "no_score_no_weights_no_classification",
                "not_an_investment_recommendation",
            ),
            "stores": ("macro",),
            "datasets": conditions,
            "example": {**snapshot, "include_context": False},
            "estimator": "unscored_liquidity_credit_vector_v2",
        },
        {
            "name": "energy.get_electricity_retail_sales",
            "kind": "energy_electricity_retail_v2",
            "type": "EnergyElectricityRetailArgumentsV2",
            "description": (
                "Read one retained U.S. all-sector EIA electricity retail "
                "metric with explicit local-capture availability."
            ),
            "assumptions": (
                "retained_stage11_us_all_sector_scope",
                "one_source_native_metric_per_call",
                "no_unit_conversion_or_interpolation",
            ),
            "stores": ("macro",),
            "datasets": (
                "macro.eia.electricity_retail_history",
                "macro.eia.electricity_retail_history_evidence",
            ),
            "example": {
                "metric": "sales",
                "mode": "latest",
                "as_of": None,
                "date_only_policy": "completed_date",
                "limit": 100,
                "start_date": None,
                "end_date": None,
            },
            "max_rows": 1_000,
            "estimator": "raw_stage11_eia_retail_metric_v2",
        },
        {
            "name": "energy.get_weekly_fundamentals",
            "kind": "energy_weekly_fundamentals_v2",
            "type": "EnergyWeeklyFundamentalsArgumentsV2",
            "description": (
                "Read the retained weekly U.S. EIA petroleum-stock history "
                "with explicit local-capture availability."
            ),
            "assumptions": (
                "retained_stage11_weekly_petroleum_scope",
                "source_native_values",
                "no_unit_conversion_or_interpolation",
            ),
            "stores": ("macro",),
            "datasets": (
                "macro.eia.petroleum_weekly_stock_history",
                "macro.eia.petroleum_weekly_stock_history_evidence",
            ),
            "example": {
                "mode": "latest",
                "as_of": None,
                "date_only_policy": "completed_date",
                "limit": 100,
                "start_date": None,
                "end_date": None,
            },
            "max_rows": 5_000,
            "estimator": "raw_stage11_eia_weekly_fundamental_v2",
        },
        {
            "name": "company.get_fundamentals",
            "kind": "company_fundamentals_v2",
            "type": "CompanyFundamentalsArgumentsV2",
            "description": (
                "Read normalized reviewed SEC facts for one exact CIK while "
                "preserving period, unit, mapping, and source semantics."
            ),
            "assumptions": (
                "query_is_one_exact_ten_digit_sec_cik",
                "reviewed_sec_core_metric_mappings_only",
                "no_ratios_ttm_calendarization_or_inferred_adjustments",
            ),
            "stores": ("company",),
            "datasets": ("fixture.company.fundamentals",),
            "example": {
                "cik": "0000320193",
                "mode": "latest",
                "as_of": None,
                "date_only_policy": "completed_date",
                "limit": 100,
                "metric_codes": [],
                "start_date": None,
                "end_date": None,
            },
            "max_rows": 1_000,
            "estimator": "normalized_reviewed_sec_fact_reader_v2",
        },
    )

    specs_by_name = {str(spec["name"]): spec for spec in specs}
    specs = tuple(
        specs_by_name[name] for name in VERSIONED_INVESTMENT_ANALYSIS_TOOLS
    )

    analytics_v21 = {
        "market.cross_sectional_performance": {
            "kind": "stage10_cross_sectional_analytics_v2_1",
            "type": "Stage10CrossSectionalAnalyticsArgumentsV21",
            "description": (
                "Measure explicit-symbol breadth, trailing realized volatility, "
                "benchmark-relative return, and rolling beta."
            ),
            "assumptions": (
                "explicit_symbols_and_benchmark_only",
                "sample_daily_return_volatility_annualized_by_sqrt_252",
                "rolling_beta_uses_aligned_trailing_simple_returns",
                "breadth_is_a_count_and_fraction_not_a_portfolio",
                "no_imputation_prediction_or_position_semantics",
            ),
            "example": {
                "tickers": ["AAPL", "MSFT"],
                "benchmark_ticker": "MSFT",
                "window": 20,
                "mode": "latest",
                "as_of": None,
                "date_only_policy": "completed_date",
                "limit": 100,
                "start_date": None,
                "end_date": None,
            },
            "estimator": "explicit_breadth_realized_volatility_relative_strength_beta_v2_1",
            "returns": "daily_and_endpoint_close_to_close_simple_returns",
        },
        "energy.get_electricity_retail_sales": {
            "kind": "energy_electricity_retail_v2",
            "type": "EnergyElectricityRetailArgumentsV2",
            "description": (
                "Compare retained monthly electricity observations with the "
                "same series twelve observations earlier."
            ),
            "assumptions": (
                "fixed_twelve_observation_same_series_comparison",
                "source_native_units_no_interpolation_or_decomposition",
                "zero_reference_levels_are_reported_not_divided",
            ),
            "example_from_v2": True,
            "estimator": "fixed_lag_monthly_source_seasonality_v2_1",
            "returns": "not_applicable",
        },
        "energy.get_weekly_fundamentals": {
            "kind": "energy_weekly_fundamentals_v2",
            "type": "EnergyWeeklyFundamentalsArgumentsV2",
            "description": (
                "Compare retained weekly energy observations with the same "
                "series fifty-two observations earlier."
            ),
            "assumptions": (
                "fixed_fifty_two_observation_same_series_comparison",
                "source_native_units_no_interpolation_or_decomposition",
                "zero_reference_levels_are_reported_not_divided",
            ),
            "example_from_v2": True,
            "estimator": "fixed_lag_weekly_source_seasonality_v2_1",
            "returns": "not_applicable",
        },
        "company.get_fundamentals": {
            "kind": "company_fundamental_ratios_v2_1",
            "type": "CompanyFundamentalRatiosArgumentsV21",
            "description": (
                "Compute reviewed same-period net margin and liabilities-to-assets "
                "ratios from normalized SEC facts."
            ),
            "assumptions": (
                "net_margin_requires_same_duration_start_and_end",
                "liabilities_to_assets_requires_same_instant_period_end",
                "same_base_unit_required",
                "no_ttm_calendarization_average_balance_or_imputation",
            ),
            "example": {
                "cik": "0000320193",
                "mode": "latest",
                "as_of": None,
                "date_only_policy": "completed_date",
                "ratio_codes": ["net_margin", "liabilities_to_assets"],
                "limit": 100,
                "start_date": None,
                "end_date": None,
            },
            "estimator": "same_period_normalized_sec_ratios_v2_1",
            "returns": "not_applicable",
        },
    }

    policies: list[dict[str, Any]] = []
    for spec in specs:
        name = str(spec["name"])
        graph = f"tool_platform.{name}.v2"
        output_schema = query_result_schema(name, analysis_series_schema)
        output_schema["properties"]["series"]["maxItems"] = 0
        availability = copy.deepcopy(spec.get("availability", temporal))
        family = _family(name)
        variant = {
            "id": name,
            "family": family,
            "api_version": "1.0",
            "version": "2.0.0",
            "operation_version": "2.0.0",
            "lifecycle": "experimental",
            "compatibility": {
                "status": "successor_breaking_v2",
                "predecessor": "1.0.0",
            },
            "description": spec["description"],
            "assumptions": [
                "host_selected_stores",
                "read_only_canonical_selection",
                "explicit_missingness_no_fill",
                *spec["assumptions"],
            ],
            "handler": graph,
            "operation_graph_id": graph,
            "read_only": True,
            "stores": list(spec["stores"]),
            "datasets": list(spec["datasets"]),
            "input_type": spec["type"],
            "input_schema_id": _versioned_schema_id(name, "input"),
            "input_schema": typed_input_schema(spec["kind"], {}),
            "output_type": "QueryResultV1",
            "output_schema_id": _versioned_schema_id(name, "output"),
            "output_schema": output_schema,
            "examples": [copy.deepcopy(spec["example"])],
            "workload_bounds": {
                "max_rows": spec.get("max_rows", 100),
                "max_series": spec.get(
                    "max_series",
                    20
                    if name
                    in (
                        *VERSIONED_MACRO_CONDITION_TOOLS,
                        *VERSIONED_RATE_TOOLS,
                        *VERSIONED_RESEARCH_STATE_TOOLS,
                    )
                    else 1,
                ),
                "max_operations": 5_000_000,
                "max_request_bytes": 8_388_608,
                "max_response_bytes": 8_388_608,
            },
            "cost_model": {
                "expression": "rows + series + operations",
                "deterministic": True,
            },
            "timeout_class": "interactive_5s",
            "availability_policy": availability,
            "live_capability": {
                "possible": False,
                "capability_id": None,
                "offline_status": "not_applicable",
            },
            "contracts": {
                "availability": "source_native_stored_availability",
                "point_in_time": (
                    "explicit"
                    if "as_of" in availability.get("modes", ())
                    else "not_applicable"
                ),
                "returns": (
                    "close_to_close_endpoint_simple_return"
                    if name == "market.cross_sectional_performance"
                    else "not_applicable"
                ),
                "estimator": spec["estimator"],
            },
            "composable": {
                "input_types": [],
                "output_types": ["QueryResultV1"],
            },
            "observability": "metadata_only",
            "owner": (
                "macro"
                if family in {"rates", "energy"}
                else "tool_platform"
                if family == "research"
                else family
            ),
            "review_requirements": ["schema", "semantics", "read_only"],
        }
        variants = [variant]
        if name in analytics_v21:
            extension = analytics_v21[name]
            extension_graph = f"tool_platform.{name}.v2_1"
            extension_output_schema = query_result_schema(
                name, analysis_series_schema
            )
            extension_output_schema["properties"]["series"]["maxItems"] = 0
            extension_example = (
                copy.deepcopy(spec["example"])
                if extension.get("example_from_v2")
                else copy.deepcopy(extension["example"])
            )
            variants.append(
                {
                    **copy.deepcopy(variant),
                    "version": "2.1.0",
                    "operation_version": "2.1.0",
                    "compatibility": {
                        "status": "additive_successor_v2_1",
                        "predecessor": "2.0.0",
                    },
                    "description": extension["description"],
                    "assumptions": [
                        "host_selected_stores",
                        "read_only_canonical_selection",
                        "explicit_missingness_no_fill",
                        *extension["assumptions"],
                    ],
                    "handler": extension_graph,
                    "operation_graph_id": extension_graph,
                    "input_type": extension["type"],
                    "input_schema_id": _versioned_schema_id(name, "input", "2.1.0"),
                    "input_schema": typed_input_schema(extension["kind"], {}),
                    "output_schema_id": _versioned_schema_id(name, "output", "2.1.0"),
                    "output_schema": extension_output_schema,
                    "examples": [extension_example],
                    "contracts": {
                        **copy.deepcopy(variant["contracts"]),
                        "returns": extension["returns"],
                        "estimator": extension["estimator"],
                    },
                }
            )
        policies.append(
            {
                "tool": name,
                "default_version": "1.0.0",
                "selector_field": "tool_version",
                "variants": variants,
                "deprecations": [
                    {
                        "version": "1.0.0",
                        "code": "tool_version_deprecated",
                        "message": (
                            f"{name} version 1.0.0 remains available for "
                            "compatibility; select version 2.0.0 for the "
                            "reviewed typed successor."
                        ),
                        "replacement": {"tool": name, "version": "2.0.0"},
                        "removal": {"status": "not_scheduled", "milestone": None},
                    }
                ],
            }
        )
    if tuple(policy["tool"] for policy in policies) != (
        VERSIONED_INVESTMENT_ANALYSIS_TOOLS
    ):
        raise AssertionError("Investment-analysis policy order drifted")
    return tuple(policies)


def versioned_schema_catalog(
    policies: tuple[Mapping[str, Any], ...],
    additive_entries: tuple[Mapping[str, Any], ...] = (),
) -> dict[str, Any]:
    variants = (
        tuple(
            variant
            for policy in policies
            for variant in policy["variants"]
        )
        + tuple(additive_entries)
    )
    payload = schema_catalog(variants)
    payload["schema_id"] = VERSIONED_CATALOG_ID
    payload["schema_version"] = VERSIONED_CATALOG_VERSION
    return payload


def legacy_tool_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
    """Return the exact Stage 1 registry shape from a canonical declaration."""

    return {
        key: copy.deepcopy(entry[key])
        for key in (
            "id",
            "version",
            "handler",
            "read_only",
            "datasets",
            "input_schema",
            "output_schema",
            "examples",
            "workload_bounds",
            "availability_policy",
        )
    } | {
        "handler": str(entry["id"]),
        "workload_bounds": {
            key: entry["workload_bounds"][key]
            for key in ("max_rows", "max_request_bytes", "max_response_bytes")
        },
    }


OPERATION_GRAPH_IDS = frozenset(
    profile.operation_graph_id for profile in current_tool_profiles()
)
VERSIONED_OPERATION_GRAPH_IDS = frozenset(
    [
        "tool_platform.econometrics.regression.v3",
        *(f"tool_platform.{name}.v2" for name in VERSIONED_TOOL_NAMES),
        "tool_platform.econometrics.regression.v2_1",
        "tool_platform.econometrics.rolling_regression.v2_1",
        "tool_platform.econometrics.stationarity.v2_1",
        "tool_platform.market.technical_indicators.v2_1",
        "tool_platform.market.technical_indicators.v2_2",
        "tool_platform.market.technical_indicators.v2_3",
        "tool_platform.market.technical_indicators.v2_4",
        "tool_platform.market.technical_indicators.v2_5",
        "tool_platform.market.technical_indicators.v2_6",
        "tool_platform.market.technical_indicators.v2_7",
        "tool_platform.market.cross_sectional_performance.v2_1",
        "tool_platform.energy.get_electricity_retail_sales.v2_1",
        "tool_platform.energy.get_weekly_fundamentals.v2_1",
        "tool_platform.company.get_fundamentals.v2_1",
        "tool_platform.news.search.v2_1",
    ]
)


__all__ = (
    "CATALOG_ID",
    "CATALOG_VERSION",
    "ADDITIVE_PUBLIC_TOOL_NAMES",
    "ADDITIVE_STAGE10_STATISTICS_TOOLS",
    "CURRENT_FAMILY_COUNTS",
    "CURRENT_PUBLIC_TOOL_NAMES",
    "FAMILY_COUNTS",
    "LEGACY_TOOL_NAMES",
    "OPERATION_GRAPH_IDS",
    "PUBLIC_TOOL_NAMES",
    "SCHEMA_DIALECT",
    "ToolProfile",
    "VERSIONED_CATALOG_ID",
    "VERSIONED_CATALOG_VERSION",
    "VERSIONED_CANONICAL_MACRO_TOOLS",
    "VERSIONED_COMPANY_FUNDAMENTAL_TOOLS",
    "VERSIONED_COMPANY_FILING_TOOLS",
    "VERSIONED_COMPANY_SHARE_COUNT_TOOLS",
    "VERSIONED_DATA_QUALITY_TOOLS",
    "VERSIONED_ECONOMETRICS_TOOLS",
    "VERSIONED_ENERGY_TOOLS",
    "VERSIONED_INVESTMENT_ANALYSIS_TOOLS",
    "VERSIONED_MACRO_CALENDAR_TOOLS",
    "VERSIONED_MACRO_CONDITION_TOOLS",
    "VERSIONED_MARKET_CROSS_SECTIONAL_TOOLS",
    "VERSIONED_MARKET_RETURN_TOOLS",
    "VERSIONED_RATE_TOOLS",
    "VERSIONED_RESEARCH_ANALYTIC_TOOLS",
    "VERSIONED_RESEARCH_STATE_TOOLS",
    "VERSIONED_MARKET_INSTRUMENT_SEARCH_TOOLS",
    "VERSIONED_NEWS_TOOLS",
    "VERSIONED_TECHNICAL_INDICATOR_TOOLS",
    "VERSIONED_TIMESERIES_ANALYSIS_TOOLS",
    "VERSIONED_TOOL_NAMES",
    "VERSIONED_OPERATION_GRAPH_IDS",
    "build_additive_tool_entries",
    "build_current_tool_entries",
    "build_tool_entries",
    "build_tool_version_policies",
    "legacy_tool_entry",
    "schema_catalog",
    "versioned_schema_catalog",
    "current_tool_profiles",
    "tool_profiles",
)
