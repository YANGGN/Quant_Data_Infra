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
        return f"tool_platform.{self.name}"


_DATASETS: dict[str, tuple[str, ...]] = {
    "macro.search_series": ("fixture.macro.stage3_catalog",),
    "macro.describe_series": ("fixture.macro.stage3_catalog",),
    "macro.get_series": ("fixture.macro.rtdsm_employ",),
    "macro.get_intraday_releases": ("fixture.macro.economic_calendar",),
    "macro.release_surprises": ("fixture.macro.economic_calendar",),
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
    return {
        "identifiers": ["fixture"],
        "mode": "latest",
        "as_of": None,
        "start_date": None,
        "end_date": None,
        "parameters": [],
        "limit": 100,
    }


def _schema_id(name: str, direction: str) -> str:
    return f"urn:quant-data:tool:{name}:{direction}:1.0.0"


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
    profile.operation_graph_id for profile in tool_profiles()
)


__all__ = (
    "CATALOG_ID",
    "CATALOG_VERSION",
    "FAMILY_COUNTS",
    "LEGACY_TOOL_NAMES",
    "OPERATION_GRAPH_IDS",
    "PUBLIC_TOOL_NAMES",
    "SCHEMA_DIALECT",
    "ToolProfile",
    "build_tool_entries",
    "legacy_tool_entry",
    "schema_catalog",
    "tool_profiles",
)
