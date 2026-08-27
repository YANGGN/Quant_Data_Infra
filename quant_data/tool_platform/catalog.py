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
VERSIONED_CATALOG_VERSION = "2.11.0"

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
    result.append(
        {
            "tool": name,
            "default_version": "1.0.0",
            "selector_field": "tool_version",
            "variants": [indicator_variant],
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
    "VERSIONED_DATA_QUALITY_TOOLS",
    "VERSIONED_ECONOMETRICS_TOOLS",
    "VERSIONED_MARKET_RETURN_TOOLS",
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
