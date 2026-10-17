"""Frozen registry projections, loaded only when historical compatibility is requested.

Keep the reviewed projection bodies and hashes intact. Current loading and its
validation remain in :mod:`quant_data.registry`, which re-exports these names.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from typing import Any, Mapping

from .registry import (
    ADDITIVE_DATA_STATUS_TOOLS,
    ADDITIVE_ETF_TOOLS,
    ADDITIVE_FMP_RESEARCH_TOOLS,
    ADDITIVE_FORWARD_PE_ANALYSIS_TOOLS,
    ADDITIVE_FORWARD_PE_TOOLS,
    ADDITIVE_NEWS_RESEARCH_TOOLS,
    ADDITIVE_STAGE10_STATISTICS_TOOLS,
    ADDITIVE_TRANSCRIPT_RESEARCH_TOOLS,
    ADDITIVE_TRANSCRIPT_TOOLS,
    DatasetDeclaration,
    MigrationDeclaration,
    PUBLIC_TOOL_NAMES,
    Registry,
    RegistryError,
    STAGE1_TOOL_NAMES,
    VERSIONED_CANONICAL_MACRO_TOOLS,
    VERSIONED_CATALOG_ID,
    VERSIONED_COMPANY_FILING_TOOLS,
    VERSIONED_COMPANY_SHARE_COUNT_TOOLS,
    VERSIONED_ETF_SNAPSHOT_TOOLS,
    VERSIONED_INVESTMENT_ANALYSIS_TOOLS,
    VERSIONED_MARKET_INSTRUMENT_SEARCH_TOOLS,
    VERSIONED_MARKET_RETURN_TOOLS,
    VERSIONED_NEWS_TOOLS,
    VERSIONED_OPTIONS_ACCESS_TOOLS,
    VERSIONED_RESEARCH_ANALYTIC_TOOLS,
    VERSIONED_TIMESERIES_ANALYSIS_TOOLS,
    _ALPACA_ETF_OPTION_COLLECTOR_ID,
    _ALPACA_ETF_OPTION_DATASET_IDS,
    _ALPACA_ETF_OPTION_REGISTRY_SOURCE_SHA256,
    _ALPACA_OPTION_COLLECTOR_IDS,
    _ALPACA_SPY_OPTION_COLLECTOR_ID,
    _ALPACA_SPY_OPTION_DATASET_IDS,
    _ALPACA_SPY_OPTION_REGISTRY_SOURCE_SHA256,
    _ANALYTICS_FOUNDATION_CATALOG_SOURCE_SHA256,
    _ANALYTICS_FOUNDATION_REGISTRY_SOURCE_SHA256,
    _BEA_PERSONAL_INCOME_COLLECTOR_ID,
    _BEA_PERSONAL_INCOME_DATASET_IDS,
    _BEA_PERSONAL_INCOME_REGISTRY_SOURCE_SHA256,
    _BLS_PRICE_WAGE_PRODUCTIVITY_COLLECTOR_ID,
    _BLS_PRICE_WAGE_PRODUCTIVITY_DATASET_IDS,
    _CANONICAL_ACCESS_CATALOG_SOURCE_SHA256,
    _CANONICAL_ACCESS_REGISTRY_SOURCE_SHA256,
    _COMPANY_FILING_PAGINATION_CATALOG_SOURCE_SHA256,
    _COMPANY_FILING_PAGINATION_REGISTRY_SOURCE_SHA256,
    _COMPANY_MARKET_COLLECTOR_SPECS,
    _COMPANY_MARKET_REGISTRY_SOURCE_SHA256,
    _CURRENT_MULTI_SOURCE_CATALOG_SOURCE_SHA256,
    _CURRENT_MULTI_SOURCE_COLLECTOR_ID,
    _CURRENT_MULTI_SOURCE_DATASET_IDS,
    _CURRENT_MULTI_SOURCE_DATASET_RELATIONS,
    _CURRENT_MULTI_SOURCE_MIGRATION_ID,
    _CURRENT_MULTI_SOURCE_REGISTRY_SOURCE_SHA256,
    _CURRENT_NEWS_CATALOG_SOURCE_SHA256,
    _CURRENT_NEWS_REGISTRY_SOURCE_SHA256,
    _DATA_STATUS_OPTIONS_CATALOG_SOURCE_SHA256,
    _DATA_STATUS_OPTIONS_REGISTRY_SOURCE_SHA256,
    _ECONOMETRICS_MODEL_SUITE_V3_CATALOG_SOURCE_SHA256,
    _ECONOMETRICS_MODEL_SUITE_V3_REGISTRY_SOURCE_SHA256,
    _ECONOMETRICS_V2_REGISTRY_SOURCE_SHA256,
    _EMPLOYMENT_VINTAGE_COLLECTOR_IDS,
    _EMPLOYMENT_VINTAGE_MIGRATION_ID,
    _ETF_SNAPSHOT_REGISTRY_SOURCE_SHA256,
    _FMP_CALENDAR_INCREMENTAL_DATASET_IDS,
    _FMP_CALENDAR_INCREMENTAL_EVENT_DATASET_ID,
    _FMP_CALENDAR_INCREMENTAL_EVIDENCE_DATASET_ID,
    _FMP_CALENDAR_INCREMENTAL_MIGRATION_ID,
    _FMP_CALENDAR_INCREMENTAL_MIGRATION_RESOURCE,
    _FMP_CALENDAR_INCREMENTAL_MIGRATION_SHA256,
    _FMP_CALENDAR_INCREMENTAL_REGISTRY_SOURCE_SHA256,
    _FMP_EMPLOYMENT_CALENDAR_COLLECTOR_ID,
    _FMP_GDP_CPI_CALENDAR_COLLECTOR_ID,
    _FMP_GDP_CPI_CALENDAR_DATASET_ID,
    _FMP_GDP_CPI_OFFICIAL_DATASET_ID,
    _FMP_GDP_CPI_SURPRISE_TOOL_ID,
    _FMP_STOCK_LATEST_COLLECTOR_ID,
    _FMP_STOCK_LATEST_CURRENT_COLLECTOR_ID,
    _FMP_STOCK_LATEST_CURRENT_DATASET_IDS,
    _FMP_STOCK_LATEST_CURRENT_DATASET_RELATIONS,
    _FMP_STOCK_LATEST_CURRENT_MIGRATION_ID,
    _FMP_STOCK_LATEST_DATASET_IDS,
    _FMP_STOCK_LATEST_MIGRATION_ID,
    _FMP_TREASURY_YIELD_CURVE_COLLECTOR_ID,
    _FMP_TREASURY_YIELD_CURVE_DATASET_IDS,
    _FMP_WHOLESALE_CALENDAR_COLLECTOR_ID,
    _FMP_WHOLESALE_CALENDAR_DATASET_ID,
    _FMP_WHOLESALE_CALENDAR_MIGRATION_ID,
    _FMP_WHOLESALE_CALENDAR_RELATIONS,
    _GDI_VINTAGE_MIGRATION_ID,
    _GDI_VINTAGE_MIGRATION_RESOURCE,
    _GDI_VINTAGE_MIGRATION_SHA256,
    _GDI_VINTAGE_REGISTRY_SOURCE_SHA256,
    _INVESTMENT_ANALYSIS_V2_CATALOG_SOURCE_SHA256,
    _INVESTMENT_ANALYSIS_V2_REGISTRY_SOURCE_SHA256,
    _IWM_ETF_DAILY_HISTORY_BACKFILL_COLLECTOR_ID,
    _IWM_ETF_DAILY_HISTORY_BACKFILL_DATASET_IDS,
    _IWM_ETF_DAILY_HISTORY_BACKFILL_REGISTRY_SOURCE_SHA256,
    _IWM_ETF_DAILY_HISTORY_COLLECTOR_ID,
    _IWM_ETF_DAILY_HISTORY_DATASET_IDS,
    _IWM_ETF_DAILY_HISTORY_REGISTRY_SOURCE_SHA256,
    _LEGACY_FMP_NEWS_DATASET_ID,
    _LEGACY_FMP_NEWS_DATASET_RELATIONS,
    _LEGACY_FMP_NEWS_MIGRATION_ID,
    _MACRO_DATABASE_EXPANSION_COLLECTOR_IDS,
    _MACRO_DATABASE_EXPANSION_COLLECTOR_SPECS,
    _MACRO_DATABASE_EXPANSION_DATASET_IDS,
    _MACRO_DATABASE_EXPANSION_REGISTRY_SOURCE_SHA256,
    _MACRO_HISTORY_COLLECTOR_IDS,
    _MACRO_HISTORY_MIGRATION_ID,
    _MACRO_VINTAGE_COLLECTOR_IDS,
    _MACRO_VINTAGE_DATASET_IDS,
    _MACRO_VINTAGE_MIGRATION_ID,
    _MARKET_AVAILABLE_TICKER_V1_CATALOG_SOURCE_SHA256,
    _MARKET_AVAILABLE_TICKER_V1_REGISTRY_SOURCE_SHA256,
    _MARKET_PRICE_SERIES_V1_CATALOG_SOURCE_SHA256,
    _MARKET_PRICE_SERIES_V1_REGISTRY_SOURCE_SHA256,
    _MARKET_RETURN_V2_REGISTRY_SOURCE_SHA256,
    _NEWS_RESEARCH_CATALOG_SOURCE_SHA256,
    _NEWS_RESEARCH_REGISTRY_SOURCE_SHA256,
    _NYFED_CMDI_COLLECTOR_ID,
    _NYFED_CMDI_DATASET_IDS,
    _NYFED_OVERNIGHT_RATES_COLLECTOR_ID,
    _NYFED_OVERNIGHT_RATES_DATASET_IDS,
    _NYFED_REPO_FACILITIES_COLLECTOR_ID,
    _NYFED_REPO_FACILITIES_DATASET_IDS,
    _NYFED_SOMA_COLLECTOR_ID,
    _NYFED_SOMA_DATASET_IDS,
    _OFFICIAL_CONDITIONS_COLLECTOR_IDS,
    _OFFICIAL_CONDITIONS_DATASET_IDS,
    _OFFICIAL_MACRO_EXTENSION_COLLECTOR_IDS,
    _OFFICIAL_MACRO_EXTENSION_DATASET_IDS,
    _OPTION_RAW_EVIDENCE_DATASET_ID,
    _OPTION_RAW_EVIDENCE_MIGRATION_ID,
    _OPTION_RAW_EVIDENCE_REGISTRY_SOURCE_SHA256,
    _OPTION_RAW_EVIDENCE_RELATIONS,
    _PLACEHOLDER_SUCCESSOR_CATALOG_SOURCE_SHA256,
    _PLACEHOLDER_SUCCESSOR_REGISTRY_SOURCE_SHA256,
    _PLACEHOLDER_SUCCESSOR_VERSIONED_TOOL_IDS,
    _POLICY_VARIANTS_2_35,
    _POLICY_VARIANTS_2_36,
    _POLICY_VARIANTS_2_37,
    _POLICY_VARIANTS_2_38,
    _POLICY_VARIANTS_2_39,
    _PRE_ANALYTICS_FOUNDATION_TOOL_NAMES,
    _PRE_BLS_PRICE_WAGE_PRODUCTIVITY_REGISTRY_SOURCE_SHA256,
    _PRE_CANONICAL_ACCESS_TOOL_NAMES,
    _PRE_DATA_STATUS_OPTIONS_TOOL_NAMES,
    _PRE_ECONOMETRICS_V2_CATALOG_SOURCE_SHA256,
    _PRE_ECONOMETRICS_V2_REGISTRY_SOURCE_SHA256,
    _PRE_EMPLOYMENT_VINTAGE_REGISTRY_SOURCE_SHA256,
    _PRE_ETF_TOOL_NAMES,
    _PRE_FMP_CALENDAR_INCREMENTAL_REGISTRY_SOURCE_SHA256,
    _PRE_FMP_EMPLOYMENT_CALENDAR_REGISTRY_SOURCE_SHA256,
    _PRE_FMP_GDP_CPI_CALENDAR_REGISTRY_SOURCE_SHA256,
    _PRE_FMP_RESEARCH_TOOL_NAMES,
    _PRE_FMP_TREASURY_YIELD_CURVE_REGISTRY_SOURCE_SHA256,
    _PRE_FMP_WHOLESALE_CALENDAR_REGISTRY_SOURCE_SHA256,
    _PRE_GDI_VINTAGE_REGISTRY_SOURCE_SHA256,
    _PRE_MACRO_HISTORY_REGISTRY_SOURCE_SHA256,
    _PRE_MACRO_VINTAGE_REGISTRY_SOURCE_SHA256,
    _PRE_MARKET_AVAILABLE_TICKER_TOOL_NAMES,
    _PRE_MARKET_RETURN_V2_REGISTRY_SOURCE_SHA256,
    _PRE_NEWS_RESEARCH_TOOL_NAMES,
    _PRE_NYFED_CMDI_REGISTRY_SOURCE_SHA256,
    _PRE_NYFED_OVERNIGHT_RATES_REGISTRY_SOURCE_SHA256,
    _PRE_NYFED_REPO_FACILITIES_REGISTRY_SOURCE_SHA256,
    _PRE_NYFED_SOMA_REGISTRY_SOURCE_SHA256,
    _PRE_OFFICIAL_CONDITIONS_REGISTRY_SOURCE_SHA256,
    _PRE_OFFICIAL_MACRO_EXTENSION_REGISTRY_SOURCE_SHA256,
    _PRE_ROBUST_ECONOMETRICS_V21_CATALOG_SOURCE_SHA256,
    _PRE_ROBUST_ECONOMETRICS_V21_REGISTRY_SOURCE_SHA256,
    _PRE_TIMESERIES_ANALYSIS_V2_CATALOG_SOURCE_SHA256,
    _PRE_TIMESERIES_ANALYSIS_V2_REGISTRY_SOURCE_SHA256,
    _REGISTRY_259_ADDITIVE_SUCCESSORS_CATALOG_SOURCE_SHA256,
    _REGISTRY_259_ADDITIVE_SUCCESSORS_SOURCE_SHA256,
    _ROBUST_ECONOMETRICS_V21_CATALOG_SOURCE_SHA256,
    _ROBUST_ECONOMETRICS_V21_REGISTRY_SOURCE_SHA256,
    _SEC_AAPL_COMPANYFACTS_COLLECTOR_ID,
    _SEC_AAPL_COMPANYFACTS_DATASET_IDS,
    _SEC_AAPL_COMPANYFACTS_REGISTRY_SOURCE_SHA256,
    _SEC_MARKET_COMPANYFACTS_COLLECTOR_ID,
    _SEC_MARKET_COMPANYFACTS_DATASET_IDS,
    _SEC_MARKET_COMPANYFACTS_REGISTRY_SOURCE_SHA256,
    _STAGE10_COLLECTOR_IDS,
    _STAGE10_DATASET_IDS,
    _STAGE10_MIGRATION_ID,
    _STAGE10_REGISTRY_SOURCE_SHA256,
    _STAGE10_RESEARCH_ANALYTICS_CATALOG_SOURCE_SHA256,
    _STAGE10_RESEARCH_ANALYTICS_REGISTRY_SOURCE_SHA256,
    _STAGE11_CANONICAL_RETRY_POLICY,
    _STAGE11_COLLECTOR_IDS,
    _STAGE11_DATASET_IDS,
    _STAGE11_HISTORICAL_RETRY_POLICY,
    _STAGE11_MIGRATION_ID,
    _STAGE11_REGISTRY_SOURCE_SHA256,
    _STAGE12A_REGISTRY_SOURCE_SHA256,
    _STAGE12B_COLLECTOR_ID,
    _STAGE12B_EXPECTED_DATASET_COLLECTOR_IDS,
    _STAGE12B_INPUT_DATASET_IDS,
    _STAGE12B_OUTPUT_DATASET_IDS,
    _STAGE12B_REGISTRY_SOURCE_SHA256,
    _STAGE12C_COLLECTOR_ID,
    _STAGE12C_EXPECTED_DATASET_COLLECTOR_IDS,
    _STAGE12C_INPUT_DATASET_IDS,
    _STAGE12C_OUTPUT_DATASET_IDS,
    _STAGE12C_REGISTRY_SOURCE_SHA256,
    _STAGE12_REGISTRY_SOURCE_SHA256,
    _STAGE1_DASHBOARD_ID,
    _STAGE2_COLLECTOR_IDS,
    _STAGE2_DATASET_IDS,
    _STAGE2_MIGRATION_IDS,
    _STAGE3_COLLECTOR_IDS,
    _STAGE3_DATASET_IDS,
    _STAGE3_MIGRATION_IDS,
    _STAGE4_COLLECTOR_IDS,
    _STAGE4_DATASET_IDS,
    _STAGE4_MIGRATION_IDS,
    _STAGE5_REGISTRY_SOURCE_SHA256,
    _STAGE6_REGISTRY_SOURCE_SHA256,
    _STAGE7_JOB_IDS,
    _STAGE7_REGISTRY_SOURCE_SHA256,
    _STAGE8_EXPORT_ID,
    _STAGE8_REGISTRY_SOURCE_SHA256,
    _STAGE9_COLLECTOR_ID,
    _STAGE9_DATASET_IDS,
    _STAGE9_MIGRATION_ID,
    _STAGE9_REGISTRY_SOURCE_SHA256,
    _STATIONARITY_V21_CATALOG_SOURCE_SHA256,
    _STATIONARITY_V21_REGISTRY_SOURCE_SHA256,
    _STRUCTURAL_BREAKS_V2_CATALOG_SOURCE_SHA256,
    _STRUCTURAL_BREAKS_V2_REGISTRY_SOURCE_SHA256,
    _TECHNICAL_INDICATORS_V21_CATALOG_SOURCE_SHA256,
    _TECHNICAL_INDICATORS_V21_REGISTRY_SOURCE_SHA256,
    _TECHNICAL_INDICATORS_V22_CATALOG_SOURCE_SHA256,
    _TECHNICAL_INDICATORS_V22_REGISTRY_SOURCE_SHA256,
    _TECHNICAL_INDICATORS_V23_CATALOG_SOURCE_SHA256,
    _TECHNICAL_INDICATORS_V23_REGISTRY_SOURCE_SHA256,
    _TECHNICAL_INDICATORS_V25_CATALOG_SOURCE_SHA256,
    _TECHNICAL_INDICATORS_V25_REGISTRY_SOURCE_SHA256,
    _TECHNICAL_INDICATORS_V26_CATALOG_SOURCE_SHA256,
    _TECHNICAL_INDICATORS_V26_REGISTRY_SOURCE_SHA256,
    _TECHNICAL_INDICATORS_V27_CATALOG_SOURCE_SHA256,
    _TECHNICAL_INDICATORS_V27_REGISTRY_SOURCE_SHA256,
    _TECHNICAL_INDICATORS_V2_CATALOG_SOURCE_SHA256,
    _TECHNICAL_INDICATORS_V2_REGISTRY_SOURCE_SHA256,
    _TIMESERIES_ANALYSIS_V2_REGISTRY_SOURCE_SHA256,
    _VERSIONED_ECONOMETRICS_TOOLS_2_34,
    _VERSIONED_TOOL_NAMES_2_35,
    build_tool_version_policies,
    legacy_tool_entry,
    remove_price_basis_policies,
)


def _stage1_dashboard_projection(
    datasets: tuple[DatasetDeclaration, ...],
    raw: dict[str, Any],
) -> tuple[
    tuple[DatasetDeclaration, ...],
    tuple[Mapping[str, Any], ...],
    dict[str, Any],
]:
    """Restore the exact historical Stage 1 dashboard declaration."""

    projected_datasets = tuple(
        replace(
            item,
            dashboard_ids=tuple(
                dashboard_id
                for dashboard_id in item.dashboard_ids
                if dashboard_id == _STAGE1_DASHBOARD_ID
            ),
        )
        for item in datasets
    )
    raw["datasets"] = [
        {
            **item,
            "dashboard_ids": [
                dashboard_id
                for dashboard_id in item["dashboard_ids"]
                if dashboard_id == _STAGE1_DASHBOARD_ID
            ],
        }
        for item in raw["datasets"]
    ]
    source = next(
        item for item in raw["dashboard"] if item["id"] == _STAGE1_DASHBOARD_ID
    )
    legacy = {
        key: copy.deepcopy(source[key])
        for key in ("id", "route", "visibility", "datasets", "tools", "relations")
    }
    legacy["api_routes"] = [
        "/api/health",
        "/api/price-series",
        "/api/agent-tools",
        "/api/agent-tools/call",
    ]
    raw["dashboard"] = [legacy]
    return projected_datasets, (legacy,), raw


def _legacy_tool_projection(
    registry: Registry,
    datasets: tuple[DatasetDeclaration, ...],
    raw: dict[str, Any],
) -> tuple[
    tuple[DatasetDeclaration, ...],
    tuple[Mapping[str, Any], ...],
    tuple[Mapping[str, Any], ...],
    dict[str, Any],
]:
    """Restore the exact two-tool Stage 1 surface inside historical profiles."""

    allowed = set(STAGE1_TOOL_NAMES)
    projected_datasets = tuple(
        replace(
            item,
            tool_ids=tuple(tool_id for tool_id in item.tool_ids if tool_id in allowed),
        )
        for item in datasets
    )
    tools = tuple(
        legacy_tool_entry(registry.tool(name)) for name in STAGE1_TOOL_NAMES
    )
    raw["schema_version"] = "1.0.0"
    raw.pop("tool_schema_catalog", None)
    raw["tools"] = [copy.deepcopy(dict(item)) for item in tools]
    raw["datasets"] = [
        {
            **item,
            "tool_ids": [
                tool_id for tool_id in item["tool_ids"] if tool_id in allowed
            ],
        }
        for item in raw["datasets"]
    ]
    projected_datasets, dashboard, raw = _stage1_dashboard_projection(
        projected_datasets, raw
    )
    raw["presentation_order"]["tools"] = list(STAGE1_TOOL_NAMES)
    return projected_datasets, tools, dashboard, raw



def _export_free_dataset_projection(
    datasets: tuple[DatasetDeclaration, ...],
    raw: dict[str, Any],
) -> tuple[tuple[DatasetDeclaration, ...], dict[str, Any]]:
    """Remove later Stage 8 consumers from immutable historical projections."""

    projected = tuple(replace(item, export_ids=()) for item in datasets)
    raw["exports"] = []
    raw["datasets"] = [
        {**item, "export_ids": []} for item in raw["datasets"]
    ]
    return projected, raw


def _tool_policy_variant_inventory(
    registry: Registry,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    return tuple(
        (
            str(policy["tool"]),
            tuple(str(variant["version"]) for variant in policy["variants"]),
        )
        for policy in registry.tool_version_policies
    )


_REGISTRY_259_ADDITIVE_VARIANTS = frozenset(
    {
        ("market.technical_indicators", "2.4.0"),
        ("market.cross_sectional_performance", "2.1.0"),
        ("energy.get_electricity_retail_sales", "2.1.0"),
        ("energy.get_weekly_fundamentals", "2.1.0"),
        ("company.get_fundamentals", "2.1.0"),
    }
)


def _pre_website_policies() -> tuple[dict[str, Any], ...]:
    from .tool_platform.retained_research_contracts import SUCCESSORS as RSUCCESSORS
    from .tool_platform.research_extensions_catalog import SUCCESSORS as ESUCCESSORS
    from .tool_platform.sharadar_company_contracts import remove_policies
    # Later opt-in policies do not belong to the frozen pre-website inventory.
    policies = tuple(
        copy.deepcopy(policy)
        for policy in remove_policies(remove_price_basis_policies(build_tool_version_policies()))
        if policy["tool"] not in (
            *VERSIONED_ETF_SNAPSHOT_TOOLS, "company.get_transcript",
            *RSUCCESSORS, *ESUCCESSORS,
        )
    )
    for policy in policies:
        if policy["tool"] == "news.search":
            policy["variants"] = [v for v in policy["variants"] if v["version"] != "2.3.0"]
            policy["deprecations"][0]["replacement"]["version"] = "2.2.0"
    return policies


def _pre_registry_267_policies() -> tuple[dict[str, Any], ...]:
    return tuple(
        copy.deepcopy(dict(policy))
        for policy in _pre_website_policies()
        if policy["tool"] not in VERSIONED_OPTIONS_ACCESS_TOOLS
    )


def _pre_registry_266_policies() -> tuple[dict[str, Any], ...]:
    policies: list[dict[str, Any]] = []
    for policy in _pre_registry_267_policies():
        projected = copy.deepcopy(dict(policy))
        if projected["tool"] == "news.search":
            projected["variants"] = [
                variant
                for variant in projected["variants"]
                if variant["version"] != "2.2.0"
            ]
            projected["deprecations"][0]["message"] = (
                "news.search version 1.0.0 remains available for the frozen "
                "Stage 4 fixture; select version 2.0.0 for retained current "
                "FMP headline metadata."
            )
            projected["deprecations"][0]["replacement"] = {
                "tool": "news.search",
                "version": "2.0.0",
            }
        policies.append(projected)
    return tuple(policies)


_WEBSITE_SOURCE_REGISTRY_SHA256 = "6b6284c184d1b4cf92bab56fe7afb80de34d78a39488c96bd21be86a59db83c8"


_ETF_SNAPSHOT_V2_PREDECESSOR_SHA256 = (
    "6080f543f920d44750eebf36ee7f6de54ecb34681b5352269454d6da7c69fc97"
)
_ETF_SNAPSHOT_V2_PREDECESSOR_CATALOG_SHA256 = (
    "7c1ff084bce29dc64d3a7db793fbb05eafe99921358ab01b308a98395b5e481b"
)



def transcript_research_registry_profile(registry: Registry) -> Registry:
    """Remove only transcript research tools and the targeted-turn variant; restore exact 2.89."""
    from .company.short_description_registry import predecessor_profile
    registry = predecessor_profile(registry)
    if (registry.schema_version, registry.registry_version) != ("1.9.0", "2.90.0"):
        return registry
    from .tool_platform.generate import _REVIEWED_REGISTRY_SOURCE_SHA256
    render = lambda value: (json.dumps(value, ensure_ascii=True, indent=1, sort_keys=True)+chr(10)).encode()
    if (registry.source_sha256 != _REVIEWED_REGISTRY_SOURCE_SHA256[("1.9.0","2.90.0")]
        or hashlib.sha256(render(registry.raw)).hexdigest() != registry.source_sha256
        or tuple(dict(t) for t in registry.tools) != tuple(registry.raw["tools"])
        or tuple(dict(p) for p in registry.tool_version_policies) != tuple(registry.raw["tool_versions"])
        or {d.id:list(d.tool_ids) for d in registry.datasets} != {d["id"]:d["tool_ids"] for d in registry.raw["datasets"]}):
        raise RegistryError("Transcript research registry source drifted")
    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.89.0"
    raw["tools"] = [t for t in raw["tools"] if t["id"] not in ADDITIVE_TRANSCRIPT_RESEARCH_TOOLS]
    raw["presentation_order"]["tools"] = [n for n in raw["presentation_order"]["tools"] if n not in ADDITIVE_TRANSCRIPT_RESEARCH_TOOLS]
    raw["tool_versions"] = [p for p in raw["tool_versions"] if p["tool"] != "company.get_transcript"]
    for dataset in raw["datasets"]:
        dataset["tool_ids"] = [n for n in dataset["tool_ids"] if n not in ADDITIVE_TRANSCRIPT_RESEARCH_TOOLS]
    raw["tool_version_schema_catalog"] = {'resource': 'quant_data/generated/tool_contract_schemas_v2.json', 'schema_id': 'quant_data.tool_contract_catalog.v2', 'schema_version': '2.35.0', 'sha256': '89cb46f59bdd1a6719f9e71482a0be82582569442e10ee6f184e2ef011ced974'}
    source = _REVIEWED_REGISTRY_SOURCE_SHA256[("1.9.0","2.89.0")]
    if hashlib.sha256(render(raw)).hexdigest() != source:
        raise RegistryError("Transcript research predecessor projection drifted")
    return replace(registry, registry_version="2.89.0", raw=raw, source_sha256=source,
        tools=tuple(t for t in registry.tools if t["id"] not in ADDITIVE_TRANSCRIPT_RESEARCH_TOOLS),
        tool_version_policies=tuple(p for p in registry.tool_version_policies if p["tool"] != "company.get_transcript"),
        datasets=tuple(replace(d, tool_ids=tuple(n for n in d.tool_ids if n not in ADDITIVE_TRANSCRIPT_RESEARCH_TOOLS)) for d in registry.datasets))


def forward_pe_analysis_registry_profile(registry: Registry) -> Registry:
    """Remove only the forward-P/E analysis tool; reproduce exact registry 2.88."""
    registry = transcript_research_registry_profile(registry)
    if (registry.schema_version,registry.registry_version)!=("1.9.0","2.89.0"):
        return registry
    from .tool_platform.generate import _REVIEWED_REGISTRY_SOURCE_SHA256
    render=lambda value:(json.dumps(value,ensure_ascii=True,indent=1,sort_keys=True)+chr(10)).encode()
    if (registry.source_sha256!=_REVIEWED_REGISTRY_SOURCE_SHA256[("1.9.0","2.89.0")]
            or hashlib.sha256(render(registry.raw)).hexdigest()!=registry.source_sha256
            or tuple(dict(t) for t in registry.tools)!=tuple(registry.raw["tools"])
            or tuple(dict(p) for p in registry.tool_version_policies)!=tuple(registry.raw["tool_versions"])):
        raise RegistryError("Forward P/E public-tool registry source drifted")
    raw=copy.deepcopy(dict(registry.raw))
    raw["registry_version"]="2.88.0"
    raw["tools"]=[t for t in raw["tools"] if t["id"] not in ADDITIVE_FORWARD_PE_ANALYSIS_TOOLS]
    raw["presentation_order"]["tools"]=[n for n in raw["presentation_order"]["tools"] if n not in ADDITIVE_FORWARD_PE_ANALYSIS_TOOLS]
    raw["tool_version_schema_catalog"]={'resource': 'quant_data/generated/tool_contract_schemas_v2.json', 'schema_id': 'quant_data.tool_contract_catalog.v2', 'schema_version': '2.34.0', 'sha256': '4eedf12a76bc55319c0a7360e18a9d12f5090215e772cf3d75c5a2bcadc63978'}
    source=_REVIEWED_REGISTRY_SOURCE_SHA256[("1.9.0","2.88.0")]
    if hashlib.sha256(render(raw)).hexdigest()!=source:
        raise RegistryError("Forward P/E public-tool predecessor projection drifted")
    return replace(registry,registry_version="2.88.0",raw=raw,source_sha256=source,
        tools=tuple(t for t in registry.tools if t["id"] not in ADDITIVE_FORWARD_PE_ANALYSIS_TOOLS))


def forward_pe_tools_registry_profile(registry: Registry) -> Registry:
    """Remove only the saved forward-P/E tool; reproduce exact registry 2.87."""
    registry = forward_pe_analysis_registry_profile(registry)
    if (registry.schema_version,registry.registry_version)!=("1.9.0","2.88.0"):
        return registry
    from .tool_platform.generate import _REVIEWED_REGISTRY_SOURCE_SHA256
    render=lambda value:(json.dumps(value,ensure_ascii=True,indent=1,sort_keys=True)+chr(10)).encode()
    if (registry.source_sha256!=_REVIEWED_REGISTRY_SOURCE_SHA256[("1.9.0","2.88.0")]
            or hashlib.sha256(render(registry.raw)).hexdigest()!=registry.source_sha256
            or tuple(dict(t) for t in registry.tools)!=tuple(registry.raw["tools"])
            or tuple(dict(p) for p in registry.tool_version_policies)!=tuple(registry.raw["tool_versions"])):
        raise RegistryError("Forward P/E public-tool registry source drifted")
    raw=copy.deepcopy(dict(registry.raw))
    raw["registry_version"]="2.87.0"
    raw["tools"]=[t for t in raw["tools"] if t["id"] not in ADDITIVE_FORWARD_PE_TOOLS]
    raw["presentation_order"]["tools"]=[n for n in raw["presentation_order"]["tools"] if n not in ADDITIVE_FORWARD_PE_TOOLS]
    raw["tool_version_schema_catalog"]={'resource': 'quant_data/generated/tool_contract_schemas_v2.json', 'schema_id': 'quant_data.tool_contract_catalog.v2', 'schema_version': '2.33.0', 'sha256': '84aedd9ede6efdbc4afba7f42244842b73c81e54177ddf22f3e3b41293f343ee'}
    source=_REVIEWED_REGISTRY_SOURCE_SHA256[("1.9.0","2.87.0")]
    if hashlib.sha256(render(raw)).hexdigest()!=source:
        raise RegistryError("Forward P/E public-tool predecessor projection drifted")
    return replace(registry,registry_version="2.87.0",raw=raw,source_sha256=source,
        tools=tuple(t for t in registry.tools if t["id"] not in ADDITIVE_FORWARD_PE_TOOLS))


def sharadar_company_tools_registry_profile(registry: Registry) -> Registry:
    """Remove only Sharadar v3 readers and reproduce exact registry 2.86."""
    registry = forward_pe_tools_registry_profile(registry)
    if (registry.schema_version, registry.registry_version) != ("1.9.0", "2.87.0"):
        return registry
    from .tool_platform.generate import _REVIEWED_REGISTRY_SOURCE_SHA256
    from .tool_platform.sharadar_company_contracts import remove_policies
    render = lambda value: (json.dumps(value, ensure_ascii=True, indent=1, sort_keys=True) + chr(10)).encode()
    if (registry.source_sha256 != _REVIEWED_REGISTRY_SOURCE_SHA256[("1.9.0", "2.87.0")]
        or hashlib.sha256(render(registry.raw)).hexdigest() != registry.source_sha256
        or tuple(dict(p) for p in registry.tool_version_policies) != tuple(registry.raw["tool_versions"])
        or tuple(dict(t) for t in registry.tools) != tuple(registry.raw["tools"])
        or {d.id: list(d.tool_ids) for d in registry.datasets} != {d["id"]: d["tool_ids"] for d in registry.raw["datasets"]}):
        raise RegistryError("Sharadar public-tool registry source drifted")
    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.86.0"
    policies = remove_policies(registry.tool_version_policies)
    raw["tool_versions"] = list(policies)
    raw["tool_version_schema_catalog"] = {'resource': 'quant_data/generated/tool_contract_schemas_v2.json', 'schema_id': 'quant_data.tool_contract_catalog.v2', 'schema_version': '2.32.0', 'sha256': 'bd2588b1d9b1813472c60bd746ec6855ae5c31b715dcf7d330ef5530a9c956ec'}
    bindings = {d["id"]: [] for d in raw["datasets"]}
    for t in [*raw["tools"], *(v for p in policies for v in p["variants"])]:
        for dataset in t["datasets"]:
            if t["id"] not in bindings[dataset]:
                bindings[dataset].append(t["id"])
    for d in raw["datasets"]:
        d["tool_ids"] = bindings[d["id"]]
    source = _REVIEWED_REGISTRY_SOURCE_SHA256[("1.9.0", "2.86.0")]
    if hashlib.sha256(render(raw)).hexdigest() != source:
        raise RegistryError("Sharadar public-tool predecessor projection drifted")
    return replace(registry, registry_version="2.86.0", raw=raw, source_sha256=source,
        tool_version_policies=policies,
        datasets=tuple(replace(d, tool_ids=tuple(bindings[d.id])) for d in registry.datasets))


def transcript_tools_registry_profile(registry: Registry) -> Registry:
    """Remove only the three local transcript readers and restore exact registry 2.85."""
    registry = sharadar_company_tools_registry_profile(registry)
    if (registry.schema_version,registry.registry_version)!=("1.9.0","2.86.0"):
        return registry
    from .tool_platform.generate import _REVIEWED_REGISTRY_SOURCE_SHA256
    render=lambda value:(json.dumps(value,ensure_ascii=True,indent=1,sort_keys=True)+chr(10)).encode()
    if (registry.source_sha256!=_REVIEWED_REGISTRY_SOURCE_SHA256[("1.9.0","2.86.0")]
        or hashlib.sha256(render(registry.raw)).hexdigest()!=registry.source_sha256
        or tuple(dict(t) for t in registry.tools)!=tuple(registry.raw["tools"])
        or {d.id:list(d.tool_ids) for d in registry.datasets}!={d["id"]:d["tool_ids"] for d in registry.raw["datasets"]}):
        raise RegistryError("Transcript public-tool registry source drifted")
    raw=copy.deepcopy(dict(registry.raw))
    raw["registry_version"]="2.85.0"
    raw["tools"]=[t for t in raw["tools"] if t["id"] not in ADDITIVE_TRANSCRIPT_TOOLS]
    raw["presentation_order"]["tools"]=[n for n in raw["presentation_order"]["tools"] if n not in ADDITIVE_TRANSCRIPT_TOOLS]
    for d in raw["datasets"]:
        d["tool_ids"]=[n for n in d["tool_ids"] if n not in ADDITIVE_TRANSCRIPT_TOOLS]
    raw["tool_version_schema_catalog"]={
        "schema_id":VERSIONED_CATALOG_ID,"schema_version":"2.31.0",
        "resource":"quant_data/generated/tool_contract_schemas_v2.json",
        "sha256":"20aaade3f5f3b5795bda6ebc8d91ed026485dcf6ae6910f2316dbe3fef1310e3",
    }
    source=_REVIEWED_REGISTRY_SOURCE_SHA256[("1.9.0","2.85.0")]
    if hashlib.sha256(render(raw)).hexdigest()!=source:
        raise RegistryError("Transcript public-tool predecessor projection drifted")
    return replace(registry,registry_version="2.85.0",raw=raw,source_sha256=source,
        tools=tuple(t for t in registry.tools if t["id"] not in ADDITIVE_TRANSCRIPT_TOOLS),
        datasets=tuple(replace(d,tool_ids=tuple(n for n in d.tool_ids if n not in ADDITIVE_TRANSCRIPT_TOOLS)) for d in registry.datasets))

def price_basis_registry_profile(registry: Registry) -> Registry:
    """Remove only close metadata successors and reproduce exact registry 2.84."""
    registry = transcript_tools_registry_profile(registry)
    if (registry.schema_version, registry.registry_version) != ("1.9.0", "2.85.0"):
        return registry
    from .tool_platform.generate import _REVIEWED_REGISTRY_SOURCE_SHA256
    render = lambda value: (
        json.dumps(value, ensure_ascii=True, indent=1, sort_keys=True) + "\n"
    ).encode()
    source = _REVIEWED_REGISTRY_SOURCE_SHA256[("1.9.0", "2.85.0")]
    if (registry.source_sha256 != source or hashlib.sha256(render(registry.raw)).hexdigest() != source
            or tuple(dict(p) for p in registry.tool_version_policies) != tuple(registry.raw["tool_versions"])):
        raise RegistryError("Price metadata registry source drifted")
    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.84.0"
    policies = remove_price_basis_policies(registry.tool_version_policies)
    raw["tool_versions"] = list(policies)
    raw["tool_version_schema_catalog"] = {
        "schema_id": VERSIONED_CATALOG_ID, "schema_version": "2.30.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": "978c7957a996753386116a9cdc9557cac5efc22ad6482d8abdc3523a0e1a761d",
    }
    predecessor = "7c729d2d80d84faa121ef33986d68590bebf7b0018ade76d641c00214b783f0c"
    if hashlib.sha256(render(raw)).hexdigest() != predecessor:
        raise RegistryError("Price metadata predecessor projection drifted")
    return replace(registry, registry_version="2.84.0", raw=raw,
                   source_sha256=predecessor, tool_version_policies=policies)


def etf_snapshot_v2_registry_profile(registry: Registry) -> Registry:
    """Remove only ETF v2 policy declarations and reproduce exact 2.83."""

    registry = price_basis_registry_profile(registry)
    if (registry.schema_version, registry.registry_version) != ("1.9.0", "2.84.0"):
        return registry
    from .tool_platform.generate import _REVIEWED_REGISTRY_SOURCE_SHA256

    render = lambda value: (
        json.dumps(value, ensure_ascii=True, indent=1, sort_keys=True) + "\n"
    ).encode()
    source = _REVIEWED_REGISTRY_SOURCE_SHA256[("1.9.0", "2.84.0")]
    if (
        registry.source_sha256 != source
        or hashlib.sha256(render(registry.raw)).hexdigest() != source
    ):
        raise RegistryError("ETF v2 registry source drifted")
    if tuple(dict(policy) for policy in registry.tool_version_policies) != tuple(
        registry.raw["tool_versions"]
    ):
        raise RegistryError("ETF v2 parsed version policies drifted")

    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.83.0"
    raw["tool_versions"] = [
        policy
        for policy in raw["tool_versions"]
        if policy["tool"] not in VERSIONED_ETF_SNAPSHOT_TOOLS
    ]
    raw["tool_version_schema_catalog"] = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.29.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _ETF_SNAPSHOT_V2_PREDECESSOR_CATALOG_SHA256,
    }
    if (
        hashlib.sha256(render(raw)).hexdigest()
        != _ETF_SNAPSHOT_V2_PREDECESSOR_SHA256
    ):
        raise RegistryError("ETF v2 predecessor projection drifted")
    return replace(
        registry,
        registry_version="2.83.0",
        raw=raw,
        source_sha256=_ETF_SNAPSHOT_V2_PREDECESSOR_SHA256,
        tool_version_policies=tuple(
            policy
            for policy in registry.tool_version_policies
            if policy["tool"] not in VERSIONED_ETF_SNAPSHOT_TOOLS
        ),
    )

def structured_transcript_registry_profile(registry: Registry) -> Registry:
    """Remove only structured transcript declarations and reproduce exact 2.82."""
    registry = etf_snapshot_v2_registry_profile(registry)
    if (registry.schema_version,registry.registry_version)!=("1.9.0","2.83.0"):
        return registry
    from .company.transcript_structured_registry import DATASET,MIGRATION_ID,PREDECESSOR_SHA256
    COLLECTOR_IDS=()
    DATASET_IDS=(DATASET,)
    from .tool_platform.generate import _REVIEWED_REGISTRY_SOURCE_SHA256
    render=lambda value:(json.dumps(value,ensure_ascii=True,indent=1,sort_keys=True)+"\n").encode()
    source=_REVIEWED_REGISTRY_SOURCE_SHA256[("1.9.0","2.83.0")]
    if registry.source_sha256!=source or hashlib.sha256(render(registry.raw)).hexdigest()!=source:
        raise RegistryError("Structured transcript registry source drifted")
    if ([dict(c) for c in registry.collectors]!=registry.raw["collectors"]
        or {d.id:(list(d.tool_ids),list(d.collector_ids)) for d in registry.datasets}
          !={d["id"]:(d["tool_ids"],d["collector_ids"]) for d in registry.raw["datasets"]}):
        raise RegistryError("Structured transcript parsed bindings drifted")
    if ({s.id:list(s.migration_order) for s in registry.stores}!={s["id"]:s["migration_order"] for s in registry.raw["stores"]}
        or {m.id:(m.store,m.ordinal,m.resource,m.sha256,m.semantic_scope,list(m.dependencies),m.reconstruction_state) for m in registry.migrations}
           !={m["id"]:(m["store"],m["ordinal"],m["resource"],m["sha256"],m["semantic_scope"],m["dependencies"],m["reconstruction_state"]) for m in registry.raw["migrations"]}):
        raise RegistryError("Structured transcript parsed migration bindings drifted")
    raw=copy.deepcopy(dict(registry.raw))
    raw["registry_version"]="2.82.0"
    raw["migrations"]=[m for m in raw["migrations"] if m["id"]!=MIGRATION_ID]
    for store in raw["stores"]:
        store["migration_order"]=[m for m in store["migration_order"] if m!=MIGRATION_ID]
    raw["collectors"]=[c for c in raw["collectors"] if c["id"] not in COLLECTOR_IDS]
    raw["datasets"]=[d for d in raw["datasets"] if d["id"] not in DATASET_IDS]
    for d in raw["datasets"]:
        d["collector_ids"]=[c for c in d["collector_ids"] if c not in COLLECTOR_IDS]
    if hashlib.sha256(render(raw)).hexdigest()!=PREDECESSOR_SHA256:
        raise RegistryError("Structured transcript predecessor projection drifted")
    return replace(registry,registry_version="2.82.0",raw=raw,source_sha256=PREDECESSOR_SHA256,
        migrations=tuple(m for m in registry.migrations if m.id!=MIGRATION_ID),
        stores=tuple(replace(s,migration_order=tuple(m for m in s.migration_order if m!=MIGRATION_ID)) for s in registry.stores),
        collectors=tuple(c for c in registry.collectors if c["id"] not in COLLECTOR_IDS),
        datasets=tuple(replace(d,collector_ids=tuple(c for c in d.collector_ids if c not in COLLECTOR_IDS)) for d in registry.datasets if d.id not in DATASET_IDS))


def sharadar_direct_registry_profile(registry: Registry) -> Registry:
    """Remove only direct delivery declarations and reproduce exact 2.81."""
    registry = structured_transcript_registry_profile(registry)
    if (registry.schema_version,registry.registry_version)!=("1.9.0","2.82.0"):
        return registry
    from .company.sharadar_direct_registry import COLLECTOR_IDS,MIGRATION_ID,PREDECESSOR_SHA256
    DATASET_IDS=()
    from .tool_platform.generate import _REVIEWED_REGISTRY_SOURCE_SHA256
    render=lambda value:(json.dumps(value,ensure_ascii=True,indent=1,sort_keys=True)+"\n").encode()
    source=_REVIEWED_REGISTRY_SOURCE_SHA256[("1.9.0","2.82.0")]
    if registry.source_sha256!=source or hashlib.sha256(render(registry.raw)).hexdigest()!=source:
        raise RegistryError("Sharadar direct registry source drifted")
    if ([dict(c) for c in registry.collectors]!=registry.raw["collectors"]
        or {d.id:(list(d.tool_ids),list(d.collector_ids)) for d in registry.datasets}
          !={d["id"]:(d["tool_ids"],d["collector_ids"]) for d in registry.raw["datasets"]}):
        raise RegistryError("Sharadar direct parsed bindings drifted")
    if ({s.id:list(s.migration_order) for s in registry.stores}!={s["id"]:s["migration_order"] for s in registry.raw["stores"]}
        or {m.id:(m.store,m.ordinal,m.resource,m.sha256,m.semantic_scope,list(m.dependencies),m.reconstruction_state) for m in registry.migrations}
           !={m["id"]:(m["store"],m["ordinal"],m["resource"],m["sha256"],m["semantic_scope"],m["dependencies"],m["reconstruction_state"]) for m in registry.raw["migrations"]}):
        raise RegistryError("Sharadar direct parsed migration bindings drifted")
    raw=copy.deepcopy(dict(registry.raw))
    raw["registry_version"]="2.81.0"
    raw["migrations"]=[m for m in raw["migrations"] if m["id"]!=MIGRATION_ID]
    for store in raw["stores"]:
        store["migration_order"]=[m for m in store["migration_order"] if m!=MIGRATION_ID]
    raw["collectors"]=[c for c in raw["collectors"] if c["id"] not in COLLECTOR_IDS]
    raw["datasets"]=[d for d in raw["datasets"] if d["id"] not in DATASET_IDS]
    for d in raw["datasets"]:
        d["collector_ids"]=[c for c in d["collector_ids"] if c not in COLLECTOR_IDS]
    if hashlib.sha256(render(raw)).hexdigest()!=PREDECESSOR_SHA256:
        raise RegistryError("Sharadar direct predecessor projection drifted")
    return replace(registry,registry_version="2.81.0",raw=raw,source_sha256=PREDECESSOR_SHA256,
        migrations=tuple(m for m in registry.migrations if m.id!=MIGRATION_ID),
        stores=tuple(replace(s,migration_order=tuple(m for m in s.migration_order if m!=MIGRATION_ID)) for s in registry.stores),
        collectors=tuple(c for c in registry.collectors if c["id"] not in COLLECTOR_IDS),
        datasets=tuple(replace(d,collector_ids=tuple(c for c in d.collector_ids if c not in COLLECTOR_IDS)) for d in registry.datasets if d.id not in DATASET_IDS))


def fmp_research_lookup_registry_profile(registry: Registry) -> Registry:
    """Remove only the FMP research lookup-index migration and reproduce exact 2.80."""
    registry = sharadar_direct_registry_profile(registry)
    if (registry.schema_version, registry.registry_version) != ("1.9.0", "2.81.0"):
        return registry
    from .company.fmp_research_lookup_registry import MIGRATION_ID, PREDECESSOR_SHA256
    from .tool_platform.generate import _REVIEWED_REGISTRY_SOURCE_SHA256

    def render(value):
        return (json.dumps(value, ensure_ascii=True, indent=1, sort_keys=True) + "\n").encode()

    source = _REVIEWED_REGISTRY_SOURCE_SHA256[("1.9.0", "2.81.0")]
    if registry.source_sha256 != source or hashlib.sha256(render(registry.raw)).hexdigest() != source:
        raise RegistryError("FMP research lookup registry source drifted")
    if ({s.id: list(s.migration_order) for s in registry.stores}
        != {s["id"]: s["migration_order"] for s in registry.raw["stores"]}
        or {m.id: (m.store, m.ordinal, m.resource, m.sha256, m.semantic_scope,
                   list(m.dependencies), m.reconstruction_state) for m in registry.migrations}
        != {m["id"]: (m["store"], m["ordinal"], m["resource"], m["sha256"], m["semantic_scope"],
                      m["dependencies"], m["reconstruction_state"]) for m in registry.raw["migrations"]}):
        raise RegistryError("FMP research lookup parsed migration bindings drifted")
    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.80.0"
    raw["migrations"] = [m for m in raw["migrations"] if m["id"] != MIGRATION_ID]
    for store in raw["stores"]:
        store["migration_order"] = [m for m in store["migration_order"] if m != MIGRATION_ID]
    if hashlib.sha256(render(raw)).hexdigest() != PREDECESSOR_SHA256:
        raise RegistryError("FMP research lookup predecessor projection drifted")
    return replace(
        registry, registry_version="2.80.0", raw=raw, source_sha256=PREDECESSOR_SHA256,
        migrations=tuple(m for m in registry.migrations if m.id != MIGRATION_ID),
        stores=tuple(replace(s, migration_order=tuple(m for m in s.migration_order if m != MIGRATION_ID))
                     for s in registry.stores),
    )


def sec_completion_registry_profile(registry: Registry) -> Registry:
    """Remove only the SEC lookup-index migration and reproduce exact 2.79."""
    registry = fmp_research_lookup_registry_profile(registry)
    if (registry.schema_version, registry.registry_version) != ("1.9.0", "2.80.0"):
        return registry
    from .company.sec_completion_registry import MIGRATION_ID, PREDECESSOR_SHA256
    from .tool_platform.generate import _REVIEWED_REGISTRY_SOURCE_SHA256

    def render(value):
        return (json.dumps(value, ensure_ascii=True, indent=1, sort_keys=True) + "\n").encode()

    source = _REVIEWED_REGISTRY_SOURCE_SHA256[("1.9.0", "2.80.0")]
    if registry.source_sha256 != source or hashlib.sha256(render(registry.raw)).hexdigest() != source:
        raise RegistryError("SEC completion registry source drifted")
    if ({s.id: list(s.migration_order) for s in registry.stores}
        != {s["id"]: s["migration_order"] for s in registry.raw["stores"]}
        or {m.id: (m.store, m.ordinal, m.resource, m.sha256, m.semantic_scope,
                   list(m.dependencies), m.reconstruction_state) for m in registry.migrations}
        != {m["id"]: (m["store"], m["ordinal"], m["resource"], m["sha256"], m["semantic_scope"],
                      m["dependencies"], m["reconstruction_state"]) for m in registry.raw["migrations"]}):
        raise RegistryError("SEC completion parsed migration bindings drifted")
    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.79.0"
    raw["migrations"] = [m for m in raw["migrations"] if m["id"] != MIGRATION_ID]
    for store in raw["stores"]:
        store["migration_order"] = [m for m in store["migration_order"] if m != MIGRATION_ID]
    if hashlib.sha256(render(raw)).hexdigest() != PREDECESSOR_SHA256:
        raise RegistryError("SEC completion predecessor projection drifted")
    return replace(
        registry, registry_version="2.79.0", raw=raw, source_sha256=PREDECESSOR_SHA256,
        migrations=tuple(m for m in registry.migrations if m.id != MIGRATION_ID),
        stores=tuple(replace(s, migration_order=tuple(m for m in s.migration_order if m != MIGRATION_ID))
                     for s in registry.stores),
    )


def sharadar_definitions_registry_profile(registry: Registry) -> Registry:
    """Remove only the definition history declarations and reproduce exact 2.78."""
    registry = sec_completion_registry_profile(registry)
    if (registry.schema_version,registry.registry_version)!=("1.9.0","2.79.0"):
        return registry
    from .company.sharadar_definition_registry import COLLECTOR_ID,DATASET_IDS,MIGRATION_ID,PREDECESSOR_SHA256
    COLLECTOR_IDS=(COLLECTOR_ID,)
    from .tool_platform.generate import _REVIEWED_REGISTRY_SOURCE_SHA256
    render=lambda value:(json.dumps(value,ensure_ascii=True,indent=1,sort_keys=True)+"\n").encode()
    source=_REVIEWED_REGISTRY_SOURCE_SHA256[("1.9.0","2.79.0")]
    if registry.source_sha256!=source or hashlib.sha256(render(registry.raw)).hexdigest()!=source:
        raise RegistryError("Sharadar definitions registry source drifted")
    if ([dict(c) for c in registry.collectors]!=registry.raw["collectors"]
        or {d.id:(list(d.tool_ids),list(d.collector_ids)) for d in registry.datasets}
          !={d["id"]:(d["tool_ids"],d["collector_ids"]) for d in registry.raw["datasets"]}):
        raise RegistryError("Sharadar definitions parsed bindings drifted")
    if ({s.id:list(s.migration_order) for s in registry.stores}!={s["id"]:s["migration_order"] for s in registry.raw["stores"]}
        or {m.id:(m.store,m.ordinal,m.resource,m.sha256,m.semantic_scope,list(m.dependencies),m.reconstruction_state) for m in registry.migrations}
           !={m["id"]:(m["store"],m["ordinal"],m["resource"],m["sha256"],m["semantic_scope"],m["dependencies"],m["reconstruction_state"]) for m in registry.raw["migrations"]}):
        raise RegistryError("Sharadar definitions parsed migration bindings drifted")
    raw=copy.deepcopy(dict(registry.raw))
    raw["registry_version"]="2.78.0"
    raw["migrations"]=[m for m in raw["migrations"] if m["id"]!=MIGRATION_ID]
    for store in raw["stores"]:
        store["migration_order"]=[m for m in store["migration_order"] if m!=MIGRATION_ID]
    raw["collectors"]=[c for c in raw["collectors"] if c["id"] not in COLLECTOR_IDS]
    raw["datasets"]=[d for d in raw["datasets"] if d["id"] not in DATASET_IDS]
    for d in raw["datasets"]:
        d["collector_ids"]=[c for c in d["collector_ids"] if c not in COLLECTOR_IDS]
    if hashlib.sha256(render(raw)).hexdigest()!=PREDECESSOR_SHA256:
        raise RegistryError("Sharadar definitions predecessor projection drifted")
    return replace(registry,registry_version="2.78.0",raw=raw,source_sha256=PREDECESSOR_SHA256,
        migrations=tuple(m for m in registry.migrations if m.id!=MIGRATION_ID),
        stores=tuple(replace(s,migration_order=tuple(m for m in s.migration_order if m!=MIGRATION_ID)) for s in registry.stores),
        collectors=tuple(c for c in registry.collectors if c["id"] not in COLLECTOR_IDS),
        datasets=tuple(replace(d,collector_ids=tuple(c for c in d.collector_ids if c not in COLLECTOR_IDS)) for d in registry.datasets if d.id not in DATASET_IDS))


def statement_history_registry_profile(registry: Registry) -> Registry:
    """Remove only the bounded FMP history collector and reproduce exact 2.77."""
    registry=sharadar_definitions_registry_profile(registry)
    if (registry.schema_version,registry.registry_version)!=("1.9.0","2.78.0"):
        return registry
    from .company.fmp_statement_registry import COLLECTOR_IDS,MIGRATION_ID,PREDECESSOR_SHA256
    from .tool_platform.generate import _REVIEWED_REGISTRY_SOURCE_SHA256
    render=lambda value:(json.dumps(value,ensure_ascii=True,indent=1,sort_keys=True)+"\n").encode()
    source=_REVIEWED_REGISTRY_SOURCE_SHA256[("1.9.0","2.78.0")]
    if registry.source_sha256!=source or hashlib.sha256(render(registry.raw)).hexdigest()!=source:
        raise RegistryError("Statement history registry source drifted")
    if ([dict(c) for c in registry.collectors]!=registry.raw["collectors"]
        or {d.id:(list(d.tool_ids),list(d.collector_ids)) for d in registry.datasets}
          !={d["id"]:(d["tool_ids"],d["collector_ids"]) for d in registry.raw["datasets"]}):
        raise RegistryError("Statement history parsed bindings drifted")
    if ({s.id:list(s.migration_order) for s in registry.stores}!={s["id"]:s["migration_order"] for s in registry.raw["stores"]}
        or {m.id:(m.store,m.ordinal,m.resource,m.sha256,m.semantic_scope,list(m.dependencies),m.reconstruction_state) for m in registry.migrations}
           !={m["id"]:(m["store"],m["ordinal"],m["resource"],m["sha256"],m["semantic_scope"],m["dependencies"],m["reconstruction_state"]) for m in registry.raw["migrations"]}):
        raise RegistryError("Statement history parsed migration bindings drifted")
    raw=copy.deepcopy(dict(registry.raw))
    raw["registry_version"]="2.77.0"
    raw["migrations"]=[m for m in raw["migrations"] if m["id"]!=MIGRATION_ID]
    for store in raw["stores"]:
        store["migration_order"]=[m for m in store["migration_order"] if m!=MIGRATION_ID]
    raw["collectors"]=[c for c in raw["collectors"] if c["id"] not in COLLECTOR_IDS]
    for d in raw["datasets"]:
        d["collector_ids"]=[c for c in d["collector_ids"] if c not in COLLECTOR_IDS]
    if hashlib.sha256(render(raw)).hexdigest()!=PREDECESSOR_SHA256:
        raise RegistryError("Statement history predecessor projection drifted")
    return replace(registry,registry_version="2.77.0",raw=raw,source_sha256=PREDECESSOR_SHA256,
        migrations=tuple(m for m in registry.migrations if m.id!=MIGRATION_ID),
        stores=tuple(replace(s,migration_order=tuple(m for m in s.migration_order if m!=MIGRATION_ID)) for s in registry.stores),
        collectors=tuple(c for c in registry.collectors if c["id"] not in COLLECTOR_IDS),
        datasets=tuple(replace(d,collector_ids=tuple(c for c in d.collector_ids if c not in COLLECTOR_IDS)) for d in registry.datasets))


def sharadar_registry_profile(registry: Registry) -> Registry:
    """Remove only Sharadar declarations and reproduce exact 2.76."""
    registry = statement_history_registry_profile(registry)
    if (registry.schema_version,registry.registry_version) != ("1.9.0","2.77.0"):
        return registry
    from .company.sharadar_registry import DATASET_IDS, MIGRATION_ID, COLLECTOR, PREDECESSOR_SHA256
    from .tool_platform.generate import _REVIEWED_REGISTRY_SOURCE_SHA256
    render=lambda value:(json.dumps(value,ensure_ascii=True,indent=1,sort_keys=True)+"\n").encode()
    source=_REVIEWED_REGISTRY_SOURCE_SHA256[("1.9.0","2.77.0")]
    if registry.source_sha256!=source or hashlib.sha256(render(registry.raw)).hexdigest()!=source:
        raise RegistryError("Sharadar registry source drifted")
    if ([dict(c) for c in registry.collectors] != registry.raw["collectors"]
        or {d.id:(list(d.tool_ids),list(d.collector_ids)) for d in registry.datasets}
           != {d["id"]:(d["tool_ids"],d["collector_ids"]) for d in registry.raw["datasets"]}
        or {s.id:list(s.migration_order) for s in registry.stores}
           != {s["id"]:s["migration_order"] for s in registry.raw["stores"]}
        or {m.id:(m.store,m.ordinal,m.resource,m.sha256,m.semantic_scope,list(m.dependencies),m.reconstruction_state) for m in registry.migrations}
           != {m["id"]:(m["store"],m["ordinal"],m["resource"],m["sha256"],m["semantic_scope"],m["dependencies"],m["reconstruction_state"]) for m in registry.raw["migrations"]}):
        raise RegistryError("Sharadar registry parsed bindings drifted")
    raw=copy.deepcopy(dict(registry.raw))
    raw["registry_version"]="2.76.0"
    raw["datasets"]=[d for d in raw["datasets"] if d["id"] not in DATASET_IDS]
    raw["collectors"]=[c for c in raw["collectors"] if c["id"]!=COLLECTOR["id"]]
    raw["migrations"]=[m for m in raw["migrations"] if m["id"]!=MIGRATION_ID]
    for s in raw["stores"]:
        s["migration_order"]=[m for m in s["migration_order"] if m!=MIGRATION_ID]
    if hashlib.sha256(render(raw)).hexdigest()!=PREDECESSOR_SHA256:
        raise RegistryError("Sharadar predecessor projection drifted")
    return replace(registry,registry_version="2.76.0",raw=raw,source_sha256=PREDECESSOR_SHA256,
        datasets=tuple(d for d in registry.datasets if d.id not in DATASET_IDS),
        collectors=tuple(c for c in registry.collectors if c["id"]!=COLLECTOR["id"]),
        migrations=tuple(m for m in registry.migrations if m.id!=MIGRATION_ID),
        stores=tuple(replace(s,migration_order=tuple(m for m in s.migration_order if m!=MIGRATION_ID)) for s in registry.stores))


def collection_universe_registry_profile(registry: Registry) -> Registry:
    """Remove only collection-manifest declarations and reproduce exact 2.75."""
    registry = sharadar_registry_profile(registry)
    if (registry.schema_version,registry.registry_version) != ("1.9.0","2.76.0"):
        return registry
    from .market.collection_registry import DATASET_IDS, MIGRATION_ID, COLLECTOR, PREDECESSOR_SHA256
    from .tool_platform.generate import _REVIEWED_REGISTRY_SOURCE_SHA256
    render=lambda value:(json.dumps(value,ensure_ascii=True,indent=1,sort_keys=True)+"\n").encode()
    source=_REVIEWED_REGISTRY_SOURCE_SHA256[("1.9.0","2.76.0")]
    if registry.source_sha256!=source or hashlib.sha256(render(registry.raw)).hexdigest()!=source:
        raise RegistryError("Collection registry source drifted")
    if ([dict(c) for c in registry.collectors] != registry.raw["collectors"]
        or {d.id:(list(d.tool_ids),list(d.collector_ids)) for d in registry.datasets}
           != {d["id"]:(d["tool_ids"],d["collector_ids"]) for d in registry.raw["datasets"]}
        or {s.id:list(s.migration_order) for s in registry.stores}
           != {s["id"]:s["migration_order"] for s in registry.raw["stores"]}
        or {m.id:(m.store,m.ordinal,m.resource,m.sha256,m.semantic_scope,list(m.dependencies),m.reconstruction_state) for m in registry.migrations}
           != {m["id"]:(m["store"],m["ordinal"],m["resource"],m["sha256"],m["semantic_scope"],m["dependencies"],m["reconstruction_state"]) for m in registry.raw["migrations"]}):
        raise RegistryError("Collection registry parsed bindings drifted")
    raw=copy.deepcopy(dict(registry.raw))
    raw["registry_version"]="2.75.0"
    raw["datasets"]=[d for d in raw["datasets"] if d["id"] not in DATASET_IDS]
    raw["collectors"]=[c for c in raw["collectors"] if c["id"]!=COLLECTOR["id"]]
    raw["migrations"]=[m for m in raw["migrations"] if m["id"]!=MIGRATION_ID]
    for s in raw["stores"]:
        s["migration_order"]=[m for m in s["migration_order"] if m!=MIGRATION_ID]
    if hashlib.sha256(render(raw)).hexdigest()!=PREDECESSOR_SHA256:
        raise RegistryError("Collection predecessor projection drifted")
    return replace(registry,registry_version="2.75.0",raw=raw,source_sha256=PREDECESSOR_SHA256,
        datasets=tuple(d for d in registry.datasets if d.id not in DATASET_IDS),
        collectors=tuple(c for c in registry.collectors if c["id"]!=COLLECTOR["id"]),
        migrations=tuple(m for m in registry.migrations if m.id!=MIGRATION_ID),
        stores=tuple(replace(s,migration_order=tuple(m for m in s.migration_order if m!=MIGRATION_ID)) for s in registry.stores))


def transcript_analysis_registry_profile(registry: Registry) -> Registry:
    """Remove only derived transcript analysis and reproduce exact 2.74."""
    registry = collection_universe_registry_profile(registry)
    if (registry.schema_version, registry.registry_version) != ("1.9.0", "2.75.0"):
        return registry
    from quant_data.company.transcript_analysis_registry import DATASET_IDS, MIGRATION_ID, COLLECTOR
    from quant_data.tool_platform.generate import _REVIEWED_REGISTRY_SOURCE_SHA256
    source = _REVIEWED_REGISTRY_SOURCE_SHA256[("1.9.0", "2.75.0")]
    render = lambda value: (json.dumps(value, ensure_ascii=True, indent=1, sort_keys=True)+"\n").encode()
    if registry.source_sha256 != source or hashlib.sha256(render(registry.raw)).hexdigest() != source:
        raise RegistryError("Transcript analysis registry source drifted")
    if ([dict(c) for c in registry.collectors] != registry.raw["collectors"]
        or {d.id: (list(d.tool_ids), list(d.collector_ids)) for d in registry.datasets}
           != {d["id"]: (d["tool_ids"], d["collector_ids"]) for d in registry.raw["datasets"]}
        or {s.id: list(s.migration_order) for s in registry.stores}
           != {s["id"]: s["migration_order"] for s in registry.raw["stores"]}
        or {m.id: (m.store, m.ordinal, m.resource, m.sha256, m.semantic_scope, list(m.dependencies), m.reconstruction_state) for m in registry.migrations}
           != {m["id"]: (m["store"], m["ordinal"], m["resource"], m["sha256"], m["semantic_scope"], m["dependencies"], m["reconstruction_state"]) for m in registry.raw["migrations"]}):
        raise RegistryError("Transcript analysis registry parsed bindings drifted")
    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.74.0"
    raw["datasets"] = [d for d in raw["datasets"] if d["id"] not in DATASET_IDS]
    raw["collectors"] = [c for c in raw["collectors"] if c["id"] != COLLECTOR["id"]]
    raw["migrations"] = [m for m in raw["migrations"] if m["id"] != MIGRATION_ID]
    for s in raw["stores"]:
        s["migration_order"] = [m for m in s["migration_order"] if m != MIGRATION_ID]
    if hashlib.sha256(render(raw)).hexdigest() != '174c4b23a1bbfccd3188d8dd944a64023dd0dad0e08fdd7cf381015a8b498b05':
        raise RegistryError("Transcript analysis predecessor projection drifted")
    return replace(registry, registry_version="2.74.0", raw=raw,
        source_sha256='174c4b23a1bbfccd3188d8dd944a64023dd0dad0e08fdd7cf381015a8b498b05',
        datasets=tuple(d for d in registry.datasets if d.id not in DATASET_IDS),
        collectors=tuple(c for c in registry.collectors if c["id"] != COLLECTOR["id"]),
        migrations=tuple(m for m in registry.migrations if m.id != MIGRATION_ID),
        stores=tuple(replace(s, migration_order=tuple(m for m in s.migration_order if m != MIGRATION_ID)) for s in registry.stores))


def equibles_transcript_registry_profile(registry: Registry) -> Registry:
    """Remove only Equibles and reproduce the exact 2.73 predecessor."""
    registry = transcript_analysis_registry_profile(registry)
    if (registry.schema_version, registry.registry_version) != ("1.9.0", "2.74.0"):
        return registry
    from quant_data.company.equibles_registry import DATASET_IDS, MIGRATION_ID, COLLECTOR
    from quant_data.tool_platform.generate import _REVIEWED_REGISTRY_SOURCE_SHA256
    source = _REVIEWED_REGISTRY_SOURCE_SHA256[("1.9.0", "2.74.0")]
    render = lambda value: (json.dumps(value, ensure_ascii=True, indent=1, sort_keys=True)+"\n").encode()
    if registry.source_sha256 != source or hashlib.sha256(render(registry.raw)).hexdigest() != source:
        raise RegistryError("Equibles registry source drifted")
    if ([dict(c) for c in registry.collectors] != registry.raw["collectors"]
        or {d.id: (list(d.tool_ids), list(d.collector_ids)) for d in registry.datasets}
           != {d["id"]: (d["tool_ids"], d["collector_ids"]) for d in registry.raw["datasets"]}
        or {s.id: list(s.migration_order) for s in registry.stores}
           != {s["id"]: s["migration_order"] for s in registry.raw["stores"]}
        or {m.id: (m.store, m.ordinal, m.resource, m.sha256, m.semantic_scope, list(m.dependencies), m.reconstruction_state) for m in registry.migrations}
           != {m["id"]: (m["store"], m["ordinal"], m["resource"], m["sha256"], m["semantic_scope"], m["dependencies"], m["reconstruction_state"]) for m in registry.raw["migrations"]}):
        raise RegistryError("Equibles registry parsed bindings drifted")
    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.73.0"
    raw["datasets"] = [d for d in raw["datasets"] if d["id"] not in DATASET_IDS]
    raw["collectors"] = [c for c in raw["collectors"] if c["id"] != COLLECTOR["id"]]
    raw["migrations"] = [m for m in raw["migrations"] if m["id"] != MIGRATION_ID]
    for s in raw["stores"]:
        s["migration_order"] = [m for m in s["migration_order"] if m != MIGRATION_ID]
    if hashlib.sha256(render(raw)).hexdigest() != '658be96e5a171801887adf6ba6c1a13e460228386423bc47e4a8b38714987a4c':
        raise RegistryError("Equibles predecessor projection drifted")
    return replace(registry, registry_version="2.73.0", raw=raw,
        source_sha256='658be96e5a171801887adf6ba6c1a13e460228386423bc47e4a8b38714987a4c',
        datasets=tuple(d for d in registry.datasets if d.id not in DATASET_IDS),
        collectors=tuple(c for c in registry.collectors if c["id"] != COLLECTOR["id"]),
        migrations=tuple(m for m in registry.migrations if m.id != MIGRATION_ID),
        stores=tuple(replace(s, migration_order=tuple(m for m in s.migration_order if m != MIGRATION_ID)) for s in registry.stores))


def analyst_history_registry_profile(registry: Registry) -> Registry:
    """Remove only analyst history and reproduce the exact 2.72 predecessor."""
    registry = equibles_transcript_registry_profile(registry)
    if (registry.schema_version, registry.registry_version) != ("1.9.0", "2.73.0"):
        return registry
    from quant_data.company.fmp_analyst_registry import DATASET_IDS, MIGRATION_ID, COLLECTOR
    from quant_data.tool_platform.generate import _REVIEWED_REGISTRY_SOURCE_SHA256
    source = _REVIEWED_REGISTRY_SOURCE_SHA256[("1.9.0", "2.73.0")]
    render = lambda value: (json.dumps(value, ensure_ascii=True, indent=1, sort_keys=True)+"\n").encode()
    if registry.source_sha256 != source or hashlib.sha256(render(registry.raw)).hexdigest() != source:
        raise RegistryError("Analyst registry source drifted")
    if ([dict(c) for c in registry.collectors] != registry.raw["collectors"]
        or {d.id: (list(d.tool_ids), list(d.collector_ids)) for d in registry.datasets}
           != {d["id"]: (d["tool_ids"], d["collector_ids"]) for d in registry.raw["datasets"]}
        or {s.id: list(s.migration_order) for s in registry.stores}
           != {s["id"]: s["migration_order"] for s in registry.raw["stores"]}
        or {m.id: (m.store, m.ordinal, m.resource, m.sha256, m.semantic_scope, list(m.dependencies), m.reconstruction_state) for m in registry.migrations}
           != {m["id"]: (m["store"], m["ordinal"], m["resource"], m["sha256"], m["semantic_scope"], m["dependencies"], m["reconstruction_state"]) for m in registry.raw["migrations"]}):
        raise RegistryError("Analyst registry parsed bindings drifted")
    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.72.0"
    raw["datasets"] = [d for d in raw["datasets"] if d["id"] not in DATASET_IDS]
    raw["collectors"] = [c for c in raw["collectors"] if c["id"] != COLLECTOR["id"]]
    raw["migrations"] = [m for m in raw["migrations"] if m["id"] != MIGRATION_ID]
    for s in raw["stores"]:
        s["migration_order"] = [m for m in s["migration_order"] if m != MIGRATION_ID]
    if hashlib.sha256(render(raw)).hexdigest() != _WEBSITE_SOURCE_REGISTRY_SHA256:
        raise RegistryError("Analyst predecessor projection drifted")
    return replace(registry, registry_version="2.72.0", raw=raw,
        source_sha256=_WEBSITE_SOURCE_REGISTRY_SHA256,
        datasets=tuple(d for d in registry.datasets if d.id not in DATASET_IDS),
        collectors=tuple(c for c in registry.collectors if c["id"] != COLLECTOR["id"]),
        migrations=tuple(m for m in registry.migrations if m.id != MIGRATION_ID),
        stores=tuple(replace(s, migration_order=tuple(m for m in s.migration_order if m != MIGRATION_ID)) for s in registry.stores))


def website_source_registry_profile(registry: Registry) -> Registry:
    """Project only the website-source extension to byte-exact registry 2.71."""
    registry = analyst_history_registry_profile(registry)
    if (registry.schema_version, registry.registry_version) != ("1.9.0", "2.72.0"):
        return registry
    render = lambda value: (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode()
    if (registry.source_sha256 != _WEBSITE_SOURCE_REGISTRY_SHA256
            or hashlib.sha256((json.dumps(registry.raw, ensure_ascii=True, indent=1, sort_keys=True) + "\n").encode()).hexdigest() != _WEBSITE_SOURCE_REGISTRY_SHA256):
        raise RegistryError("Website source registry drifted")
    if ([dict(c) for c in registry.collectors] != registry.raw["collectors"]
        or [dict(p) for p in registry.tool_version_policies] != registry.raw["tool_versions"]
        or {s.id: list(s.migration_order) for s in registry.stores}
           != {s["id"]: s["migration_order"] for s in registry.raw["stores"]}
        or {m.id: (m.store, m.ordinal, m.resource, m.sha256, m.semantic_scope,
                   list(m.dependencies), m.reconstruction_state) for m in registry.migrations}
           != {m["id"]: (m["store"], m["ordinal"], m["resource"], m["sha256"],
                         m["semantic_scope"], m["dependencies"], m["reconstruction_state"])
               for m in registry.raw["migrations"]}):
        raise RegistryError("Website source parsed bindings drifted")
    raw = copy.deepcopy(dict(registry.raw))
    migration_id = "news:0009_website_source_extension"
    raw["registry_version"] = "2.71.0"
    raw["migrations"] = [m for m in raw["migrations"] if m["id"] != migration_id]
    for store in raw["stores"]:
        store["migration_order"] = [m for m in store["migration_order"] if m != migration_id]
    for collector in raw["collectors"]:
        if collector["id"] == "news.current_multi_source":
            collector["workload_bounds"]["max_requests"] = 22
    for policy in raw["tool_versions"]:
        if policy["tool"] == "news.search":
            policy["variants"] = [v for v in policy["variants"] if v["version"] != "2.3.0"]
            policy["deprecations"][0]["replacement"]["version"] = "2.2.0"
    raw["tool_version_schema_catalog"] = {
        "schema_id": VERSIONED_CATALOG_ID, "schema_version": "2.28.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": "61755e015ba4f52eb2571d729c390137303021ff074241e5164cc36d2258f7ed",
    }
    if hashlib.sha256(render(raw)).hexdigest() != _FMP_RESEARCH_REGISTRY_SOURCE_SHA256:
        raise RegistryError("Website source predecessor projection drifted")
    return replace(
        registry, registry_version="2.71.0", raw=raw,
        source_sha256=_FMP_RESEARCH_REGISTRY_SOURCE_SHA256,
        migrations=tuple(m for m in registry.migrations if m.id != migration_id),
        stores=tuple(replace(s, migration_order=tuple(m for m in s.migration_order if m != migration_id)) for s in registry.stores),
        collectors=tuple(raw["collectors"]),
        tool_version_policies=tuple(raw["tool_versions"]),
    )


_FMP_RESEARCH_REGISTRY_SOURCE_SHA256 = "55285a106a56a3d664f83dd75cb71c43aa21f5e9637d0200704933a291732a78"

def fmp_research_registry_profile(registry: Registry) -> Registry:
    """Remove only this lane and reproduce the byte-exact 2.70 predecessor."""
    registry = website_source_registry_profile(registry)
    if (registry.schema_version, registry.registry_version) != ("1.9.0", "2.71.0"):
        return registry
    from quant_data.company.fmp_research_registry import COLLECTORS, DATASET_IDS, MIGRATION_ID
    render = lambda value: (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode()
    if (hashlib.sha256(render(registry.raw)).hexdigest() != _FMP_RESEARCH_REGISTRY_SOURCE_SHA256
        or registry.source_sha256 != _FMP_RESEARCH_REGISTRY_SOURCE_SHA256):
        raise RegistryError("FMP research registry source drifted")
    if ([dict(t) for t in registry.tools] != registry.raw["tools"]
        or [dict(c) for c in registry.collectors] != registry.raw["collectors"]
        or {d.id: (list(d.tool_ids), list(d.collector_ids)) for d in registry.datasets}
           != {d["id"]: (d["tool_ids"], d["collector_ids"]) for d in registry.raw["datasets"]}
        or {s.id: list(s.migration_order) for s in registry.stores}
           != {s["id"]: s["migration_order"] for s in registry.raw["stores"]}
        or {m.id: m.sha256 for m in registry.migrations}
           != {m["id"]: m["sha256"] for m in registry.raw["migrations"]}):
        raise RegistryError("FMP research parsed bindings drifted")
    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.70.0"
    collector_ids = {c["id"] for c in COLLECTORS}
    raw["tools"] = [t for t in raw["tools"] if t["id"] not in ADDITIVE_FMP_RESEARCH_TOOLS]
    raw["presentation_order"]["tools"] = list(_PRE_FMP_RESEARCH_TOOL_NAMES)
    raw["collectors"] = [c for c in raw["collectors"] if c["id"] not in collector_ids]
    raw["datasets"] = [d for d in raw["datasets"] if d["id"] not in DATASET_IDS]
    bindings = {}
    for d in raw["datasets"]:
        d["collector_ids"] = [c for c in d["collector_ids"] if c not in collector_ids]
        bindings[d["id"]] = tuple(d["collector_ids"])
    raw["migrations"] = [m for m in raw["migrations"] if m["id"] != MIGRATION_ID]
    for s in raw["stores"]:
        s["migration_order"] = [m for m in s["migration_order"] if m != MIGRATION_ID]
    raw["tool_version_schema_catalog"] = {
        "schema_id": VERSIONED_CATALOG_ID, "schema_version": "2.27.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": "6f143f9f32fe0cc7d713b9afb1425901ee96e3fdea1539d09f8d602ca794894b",
    }
    if hashlib.sha256(render(raw)).hexdigest() != _ETF_SNAPSHOT_REGISTRY_SOURCE_SHA256:
        raise RegistryError("FMP research historical projection drifted")
    return replace(registry, registry_version="2.70.0", raw=raw,
        source_sha256=_ETF_SNAPSHOT_REGISTRY_SOURCE_SHA256,
        tools=tuple(t for t in registry.tools if t["id"] not in ADDITIVE_FMP_RESEARCH_TOOLS),
        collectors=tuple(c for c in registry.collectors if c["id"] not in collector_ids),
        datasets=tuple(replace(d, collector_ids=bindings[d.id]) for d in registry.datasets if d.id not in DATASET_IDS),
        migrations=tuple(m for m in registry.migrations if m.id != MIGRATION_ID),
        stores=tuple(replace(s, migration_order=tuple(m for m in s.migration_order if m != MIGRATION_ID)) for s in registry.stores))


def etf_snapshot_registry_profile(registry: Registry) -> Registry:
    """Remove only the ETF public tool to reproduce the exact 2.69 predecessor."""
    registry = fmp_research_registry_profile(registry)
    if (registry.schema_version, registry.registry_version) != ("1.9.0", "2.70.0"):
        return registry
    render = lambda value: (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode()
    if (hashlib.sha256(render(registry.raw)).hexdigest() != _ETF_SNAPSHOT_REGISTRY_SOURCE_SHA256
        or registry.source_sha256 != _ETF_SNAPSHOT_REGISTRY_SOURCE_SHA256):
        raise RegistryError("ETF snapshot registry source drifted")
    if ([dict(tool) for tool in registry.tools] != registry.raw["tools"]
        or {d.id: list(d.tool_ids) for d in registry.datasets}
        != {d["id"]: d["tool_ids"] for d in registry.raw["datasets"]}):
        raise RegistryError("ETF snapshot parsed bindings drifted")
    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.69.0"
    raw["tools"] = [tool for tool in raw["tools"] if tool["id"] not in ADDITIVE_ETF_TOOLS]
    raw["presentation_order"]["tools"] = list(_PRE_ETF_TOOL_NAMES)
    raw["tool_version_schema_catalog"] = {
        "schema_id": VERSIONED_CATALOG_ID, "schema_version": "2.26.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _DATA_STATUS_OPTIONS_CATALOG_SOURCE_SHA256,
    }
    bindings = {}
    for dataset in raw["datasets"]:
        dataset["tool_ids"] = [name for name in dataset["tool_ids"] if name not in ADDITIVE_ETF_TOOLS]
        bindings[dataset["id"]] = tuple(dataset["tool_ids"])
    if hashlib.sha256(render(raw)).hexdigest() != _COMPANY_MARKET_REGISTRY_SOURCE_SHA256:
        raise RegistryError("ETF snapshot historical projection drifted")
    return replace(registry, registry_version="2.69.0", raw=raw,
        source_sha256=_COMPANY_MARKET_REGISTRY_SOURCE_SHA256,
        tools=tuple(tool for tool in registry.tools if tool["id"] not in ADDITIVE_ETF_TOOLS),
        datasets=tuple(replace(d, tool_ids=bindings[d.id]) for d in registry.datasets))


def company_market_data_registry_profile(registry: Registry) -> Registry:
    """Project additive company live collectors to byte-exact registry 2.68."""
    registry = etf_snapshot_registry_profile(registry)
    if (registry.schema_version, registry.registry_version) != ("1.9.0", "2.69.0"):
        return registry
    payload = (json.dumps(registry.raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode()
    if hashlib.sha256(payload).hexdigest() != _COMPANY_MARKET_REGISTRY_SOURCE_SHA256 or registry.source_sha256 != _COMPANY_MARKET_REGISTRY_SOURCE_SHA256:
        raise RegistryError("Company market-data registry source drifted")
    if (
        len(registry.migrations) != 44
        or len(registry.datasets) != 59
        or len(registry.collectors) != 66
        or [dict(c) for c in registry.collectors] != registry.raw["collectors"]
        or {d.id: tuple(d.collector_ids) for d in registry.datasets}
        != {d["id"]: tuple(d["collector_ids"]) for d in registry.raw["datasets"]}
    ):
        raise RegistryError("Company market-data parsed bindings drifted")
    raw = json.loads(json.dumps(registry.raw))
    raw["registry_version"] = "2.68.0"
    ids = set(_COMPANY_MARKET_COLLECTOR_SPECS)
    raw["collectors"] = [c for c in raw["collectors"] if c["id"] not in ids]
    bindings = {}
    for dataset in raw["datasets"]:
        dataset["collector_ids"] = [c for c in dataset["collector_ids"] if c not in ids]
        bindings[dataset["id"]] = tuple(dataset["collector_ids"])
    projected = (json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode()
    if hashlib.sha256(projected).hexdigest() != _MACRO_DATABASE_EXPANSION_REGISTRY_SOURCE_SHA256:
        raise RegistryError("Company market-data historical projection drifted")
    return replace(
        registry, registry_version="2.68.0", raw=raw,
        source_sha256=_MACRO_DATABASE_EXPANSION_REGISTRY_SOURCE_SHA256,
        collectors=tuple(c for c in registry.collectors if c["id"] not in ids),
        datasets=tuple(replace(d, collector_ids=bindings[d.id]) for d in registry.datasets),
    )


def macro_database_expansion_registry_profile(registry: Registry) -> Registry:
    """Project the six manual macro collectors back to exact registry 2.67."""

    registry = company_market_data_registry_profile(registry)

    version = (registry.schema_version, registry.registry_version)
    if version not in {("1.9.0", "2.68.0"), ("1.9.0", "2.67.0")}:
        return registry

    payload = (
        json.dumps(registry.raw, ensure_ascii=True, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    suffix = _MACRO_DATABASE_EXPANSION_COLLECTOR_IDS
    dataset_by_id = {item.id: item for item in registry.datasets}
    target_datasets = tuple(
        dataset_by_id.get(dataset_id)
        for dataset_id in _MACRO_DATABASE_EXPANSION_DATASET_IDS
    )
    if any(item is None for item in target_datasets):
        raise RegistryError(
            "Macro database-expansion dataset declarations drifted"
        )

    if version == ("1.9.0", "2.67.0"):
        if (
            registry.source_sha256
            != _DATA_STATUS_OPTIONS_REGISTRY_SOURCE_SHA256
            or hashlib.sha256(payload).hexdigest()
            != _DATA_STATUS_OPTIONS_REGISTRY_SOURCE_SHA256
            or len(registry.migrations) != 44
            or len(registry.datasets) != 59
            or len(registry.collectors) != 58
            or set(suffix).intersection(
                str(item["id"]) for item in registry.collectors
            )
            or any(
                set(item.collector_ids).intersection(suffix)
                for item in target_datasets
                if item is not None
            )
        ):
            raise RegistryError(
                "Historical pre-macro-database-expansion profile drifted"
            )
        return registry

    common = {
        "configuration_env": [],
        "input_datasets": [],
        "mutation_policy": {
            "mode": "append_versions_and_snapshot_membership",
            "unchanged": "zero_persistent_writes",
        },
        "network": True,
        "output_datasets": list(_MACRO_DATABASE_EXPANSION_DATASET_IDS),
        "physical_locks": "derived_from_output_store_paths",
        "retry_policy": {
            "backoff": "none_single_attempt",
            "honor_retry_after": False,
            "max_attempts": 1,
            "transient_classes": [],
        },
        "schedule_eligibility": {"mode": "manual_only"},
        "semantic_identity": {
            "excludes": [
                "captured_at",
                "http_headers",
                "source_row_order",
            ],
            "includes": [
                "request_scope",
                "normalization_version",
                "normalized_observations",
            ],
        },
        "version": "1.0.0",
    }
    expected_collectors = {
        collector_id: {
            **common,
            "handler": values[0],
            "id": collector_id,
            "workload_bounds": {
                "max_requests": values[1],
                "max_rows": values[2],
                "max_bytes": values[3],
                "max_seconds": values[4],
            },
        }
        for collector_id, values in _MACRO_DATABASE_EXPANSION_COLLECTOR_SPECS.items()
    }
    collector_by_id = {
        str(item["id"]): item for item in registry.collectors
    }
    historical_collectors: dict[str, tuple[str, ...]] = {}
    for dataset_id, dataset in zip(
        _MACRO_DATABASE_EXPANSION_DATASET_IDS,
        target_datasets,
        strict=True,
    ):
        assert dataset is not None
        if (
            dataset.collector_ids[-len(suffix) :] != suffix
            or any(
                dataset.collector_ids.count(collector_id) != 1
                for collector_id in suffix
            )
        ):
            raise RegistryError(
                "Macro database-expansion dataset binding drifted"
            )
        historical_collectors[dataset_id] = dataset.collector_ids[
            : -len(suffix)
        ]

    if (
        registry.source_sha256
        != _MACRO_DATABASE_EXPANSION_REGISTRY_SOURCE_SHA256
        or hashlib.sha256(payload).hexdigest()
        != _MACRO_DATABASE_EXPANSION_REGISTRY_SOURCE_SHA256
        or len(registry.migrations) != 44
        or len(registry.datasets) != 59
        or len(registry.collectors) != 64
        or any(
            collector_id not in collector_by_id
            or dict(collector_by_id[collector_id])
            != expected_collectors[collector_id]
            for collector_id in suffix
        )
        or any(
            step.collector_id in suffix
            for job in registry.jobs
            for step in job.steps
        )
    ):
        raise RegistryError(
            "Canonical registry cannot reproduce revision 2.67"
        )

    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.67.0"
    raw["collectors"] = [
        item for item in raw["collectors"] if item["id"] not in suffix
    ]
    raw_target_ids: set[str] = set()
    for item in raw["datasets"]:
        dataset_id = item.get("id")
        if dataset_id not in historical_collectors:
            continue
        if tuple(item.get("collector_ids", ())) != (
            historical_collectors[dataset_id] + suffix
        ):
            raise RegistryError(
                "Canonical macro database-expansion raw binding drifted"
            )
        item["collector_ids"] = list(historical_collectors[dataset_id])
        raw_target_ids.add(str(dataset_id))
    if raw_target_ids != set(_MACRO_DATABASE_EXPANSION_DATASET_IDS):
        raise RegistryError(
            "Canonical macro database-expansion dataset inventory drifted"
        )

    projected_payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(projected_payload).hexdigest()
        != _DATA_STATUS_OPTIONS_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError(
            "Macro database-expansion registry projection drifted"
        )
    return replace(
        registry,
        registry_version="2.67.0",
        datasets=tuple(
            replace(item, collector_ids=historical_collectors[item.id])
            if item.id in historical_collectors
            else item
            for item in registry.datasets
        ),
        collectors=tuple(
            item
            for item in registry.collectors
            if str(item["id"]) not in suffix
        ),
        raw=raw,
        source_sha256=_DATA_STATUS_OPTIONS_REGISTRY_SOURCE_SHA256,
    )



def data_status_options_registry_profile(registry: Registry) -> Registry:
    """Project registry 2.67 Data Status/options-v2 back to exact 2.66."""

    registry = macro_database_expansion_registry_profile(registry)
    version = (registry.schema_version, registry.registry_version)
    if version not in {("1.9.0", "2.67.0"), ("1.9.0", "2.66.0")}:
        return registry
    payload = (
        json.dumps(registry.raw, ensure_ascii=True, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    predecessor_catalog = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.25.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _NEWS_RESEARCH_CATALOG_SOURCE_SHA256,
    }
    predecessor_inventory = tuple(
        (
            str(policy["tool"]),
            tuple(str(item["version"]) for item in policy["variants"]),
        )
        for policy in _pre_registry_267_policies()
    )
    tool_names = tuple(str(item["id"]) for item in registry.tools)
    if version == ("1.9.0", "2.66.0"):
        if (
            registry.source_sha256 != _NEWS_RESEARCH_REGISTRY_SOURCE_SHA256
            or hashlib.sha256(payload).hexdigest()
            != _NEWS_RESEARCH_REGISTRY_SOURCE_SHA256
            or tool_names != _PRE_DATA_STATUS_OPTIONS_TOOL_NAMES
            or _tool_policy_variant_inventory(registry) != predecessor_inventory
            or registry.raw.get("tool_version_schema_catalog")
            != predecessor_catalog
        ):
            raise RegistryError(
                "Registry 2.66 Data Status/options predecessor identity drifted"
            )
        return registry

    current_catalog = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.26.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _DATA_STATUS_OPTIONS_CATALOG_SOURCE_SHA256,
    }
    current_inventory = tuple(
        (
            str(policy["tool"]),
            tuple(str(item["version"]) for item in policy["variants"]),
        )
        for policy in _pre_website_policies()
    )
    if (
        registry.source_sha256 != _DATA_STATUS_OPTIONS_REGISTRY_SOURCE_SHA256
        or hashlib.sha256(payload).hexdigest()
        != _DATA_STATUS_OPTIONS_REGISTRY_SOURCE_SHA256
        or tool_names != _PRE_ETF_TOOL_NAMES
        or _tool_policy_variant_inventory(registry) != current_inventory
        or registry.raw.get("tool_version_schema_catalog") != current_catalog
    ):
        raise RegistryError("Current Data Status/options registry identity drifted")

    removed_tools = set(ADDITIVE_DATA_STATUS_TOOLS)
    retained_policies = list(_pre_registry_267_policies())
    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.66.0"
    raw["tools"] = [
        item for item in raw["tools"] if item["id"] not in removed_tools
    ]
    raw["tool_versions"] = copy.deepcopy(retained_policies)
    raw["tool_version_schema_catalog"] = predecessor_catalog
    raw["presentation_order"]["tools"] = list(
        _PRE_DATA_STATUS_OPTIONS_TOOL_NAMES
    )

    tool_ids_by_dataset: dict[str, list[str]] = {
        item["id"]: [] for item in raw["datasets"]
    }
    for tool in raw["tools"]:
        for dataset_id in tool["datasets"]:
            tool_ids_by_dataset[dataset_id].append(tool["id"])
    for policy in raw["tool_versions"]:
        for variant in policy["variants"]:
            for dataset_id in variant["datasets"]:
                if variant["id"] not in tool_ids_by_dataset[dataset_id]:
                    tool_ids_by_dataset[dataset_id].append(variant["id"])
    raw["datasets"] = [
        {**item, "tool_ids": tool_ids_by_dataset[item["id"]]}
        for item in raw["datasets"]
    ]
    projected_payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(projected_payload).hexdigest()
        != _NEWS_RESEARCH_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError("Data Status/options registry projection drifted")

    tools = tuple(
        item for item in registry.tools if str(item["id"]) not in removed_tools
    )
    datasets = tuple(
        replace(item, tool_ids=tuple(tool_ids_by_dataset[item.id]))
        for item in registry.datasets
    )
    return replace(
        registry,
        registry_version="2.66.0",
        datasets=datasets,
        tools=tools,
        tool_version_policies=tuple(retained_policies),
        raw=raw,
        source_sha256=_NEWS_RESEARCH_REGISTRY_SOURCE_SHA256,
    )


def _pre_registry_264_policies() -> tuple[dict[str, Any], ...]:
    policies: list[dict[str, Any]] = []
    for policy in _pre_registry_266_policies():
        projected = copy.deepcopy(dict(policy))
        if projected["tool"] == "news.search":
            projected["variants"] = [
                variant
                for variant in projected["variants"]
                if variant["version"] != "2.1.0"
            ]
        policies.append(projected)
    return tuple(policies)


def news_research_registry_profile(registry: Registry) -> Registry:
    """Project the news research surface in registry 2.66 back to exact 2.65."""

    registry = data_status_options_registry_profile(registry)
    version = (registry.schema_version, registry.registry_version)
    if version not in {("1.9.0", "2.66.0"), ("1.9.0", "2.65.0")}:
        return registry
    payload = (
        json.dumps(registry.raw, ensure_ascii=True, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    tool_names = tuple(str(item["id"]) for item in registry.tools)
    predecessor_catalog = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.24.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _CURRENT_MULTI_SOURCE_CATALOG_SOURCE_SHA256,
    }
    predecessor_inventory = tuple(
        (
            str(policy["tool"]),
            tuple(str(item["version"]) for item in policy["variants"]),
        )
        for policy in _pre_registry_266_policies()
    )
    if version == ("1.9.0", "2.65.0"):
        if (
            registry.source_sha256 != _OPTION_RAW_EVIDENCE_REGISTRY_SOURCE_SHA256
            or hashlib.sha256(payload).hexdigest()
            != _OPTION_RAW_EVIDENCE_REGISTRY_SOURCE_SHA256
            or tool_names != _PRE_NEWS_RESEARCH_TOOL_NAMES
            or _tool_policy_variant_inventory(registry) != predecessor_inventory
            or registry.raw.get("tool_version_schema_catalog")
            != predecessor_catalog
        ):
            raise RegistryError("Registry 2.65 news-research predecessor identity drifted")
        return registry

    current_catalog = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.25.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _NEWS_RESEARCH_CATALOG_SOURCE_SHA256,
    }
    current_inventory = tuple(
        (
            str(policy["tool"]),
            tuple(str(item["version"]) for item in policy["variants"]),
        )
        for policy in _pre_registry_267_policies()
    )
    if (
        registry.source_sha256 != _NEWS_RESEARCH_REGISTRY_SOURCE_SHA256
        or hashlib.sha256(payload).hexdigest()
        != _NEWS_RESEARCH_REGISTRY_SOURCE_SHA256
        or tool_names != _PRE_DATA_STATUS_OPTIONS_TOOL_NAMES
        or _tool_policy_variant_inventory(registry) != current_inventory
        or registry.raw.get("tool_version_schema_catalog") != current_catalog
    ):
        raise RegistryError("Current news-research registry identity drifted")

    removed_tools = set(ADDITIVE_NEWS_RESEARCH_TOOLS)
    retained_policies = list(_pre_registry_266_policies())
    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.65.0"
    raw["tools"] = [
        item for item in raw["tools"] if item["id"] not in removed_tools
    ]
    raw["tool_versions"] = copy.deepcopy(retained_policies)
    raw["tool_version_schema_catalog"] = predecessor_catalog
    raw["presentation_order"]["tools"] = list(_PRE_NEWS_RESEARCH_TOOL_NAMES)

    tool_ids_by_dataset: dict[str, list[str]] = {
        item["id"]: [] for item in raw["datasets"]
    }
    for tool in raw["tools"]:
        for dataset_id in tool["datasets"]:
            tool_ids_by_dataset[dataset_id].append(tool["id"])
    for policy in raw["tool_versions"]:
        for variant in policy["variants"]:
            for dataset_id in variant["datasets"]:
                if variant["id"] not in tool_ids_by_dataset[dataset_id]:
                    tool_ids_by_dataset[dataset_id].append(variant["id"])
    raw["datasets"] = [
        {**item, "tool_ids": tool_ids_by_dataset[item["id"]]}
        for item in raw["datasets"]
    ]
    projected_payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(projected_payload).hexdigest()
        != _OPTION_RAW_EVIDENCE_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError("News-research registry projection drifted")

    tools = tuple(
        item for item in registry.tools if str(item["id"]) not in removed_tools
    )
    datasets = tuple(
        replace(item, tool_ids=tuple(tool_ids_by_dataset[item.id]))
        for item in registry.datasets
    )
    return replace(
        registry,
        registry_version="2.65.0",
        datasets=datasets,
        tools=tools,
        tool_version_policies=tuple(retained_policies),
        raw=raw,
        source_sha256=_OPTION_RAW_EVIDENCE_REGISTRY_SOURCE_SHA256,
    )
def _pre_registry_262_policies() -> tuple[dict[str, Any], ...]:
    policies: list[dict[str, Any]] = []
    for policy in (
        item
        for item in _pre_registry_267_policies()
        if item["tool"] not in VERSIONED_NEWS_TOOLS
    ):
        projected = copy.deepcopy(dict(policy))
        if projected["tool"] == "market.technical_indicators":
            projected["variants"] = [
                variant
                for variant in projected["variants"]
                if variant["version"] != "2.7.0"
            ]
        policies.append(projected)
    return tuple(policies)


def _pre_registry_261_policies() -> tuple[dict[str, Any], ...]:
    policies: list[dict[str, Any]] = []
    for policy in _pre_registry_262_policies():
        projected = copy.deepcopy(dict(policy))
        if projected["tool"] == "market.technical_indicators":
            projected["variants"] = [
                variant
                for variant in projected["variants"]
                if variant["version"] != "2.6.0"
            ]
        policies.append(projected)
    return tuple(policies)


def _pre_registry_260_policies() -> tuple[dict[str, Any], ...]:
    policies: list[dict[str, Any]] = []
    for policy in _pre_registry_261_policies():
        projected = copy.deepcopy(dict(policy))
        if projected["tool"] == "market.technical_indicators":
            projected["variants"] = [
                variant
                for variant in projected["variants"]
                if variant["version"] != "2.5.0"
            ]
        policies.append(projected)
    return tuple(policies)


def _pre_registry_259_policies() -> tuple[dict[str, Any], ...]:
    policies: list[dict[str, Any]] = []
    for policy in _pre_registry_260_policies():
        projected = copy.deepcopy(dict(policy))
        projected["variants"] = [
            variant
            for variant in projected["variants"]
            if (projected["tool"], variant["version"])
            not in _REGISTRY_259_ADDITIVE_VARIANTS
        ]
        policies.append(projected)
    return tuple(policies)


def _pre_technical_indicators_v23_policies() -> tuple[dict[str, Any], ...]:
    policies: list[dict[str, Any]] = []
    for policy in _pre_registry_259_policies():
        projected = copy.deepcopy(dict(policy))
        if projected["tool"] == "market.technical_indicators":
            projected["variants"] = [
                variant
                for variant in projected["variants"]
                if variant["version"] != "2.3.0"
            ]
        policies.append(projected)
    return tuple(policies)


def _pre_investment_analysis_policies() -> tuple[dict[str, Any], ...]:
    return tuple(
        policy
        for policy in _pre_technical_indicators_v23_policies()
        if policy["tool"] not in VERSIONED_INVESTMENT_ANALYSIS_TOOLS
    )


def option_raw_evidence_registry_profile(registry: Registry) -> Registry:
    """Project raw-option evidence registry 2.65 back to exact registry 2.64."""

    registry = news_research_registry_profile(registry)
    version = (registry.schema_version, registry.registry_version)
    if version not in {("1.9.0", "2.65.0"), ("1.9.0", "2.64.0")}:
        return registry
    payload = (
        json.dumps(registry.raw, ensure_ascii=True, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    if version == ("1.9.0", "2.64.0"):
        if (
            registry.source_sha256
            != _CURRENT_MULTI_SOURCE_REGISTRY_SOURCE_SHA256
            or hashlib.sha256(payload).hexdigest()
            != _CURRENT_MULTI_SOURCE_REGISTRY_SOURCE_SHA256
        ):
            raise RegistryError(
                "Registry 2.64 raw-option predecessor identity drifted"
            )
        return registry

    migration_by_id = {item.id: item for item in registry.migrations}
    dataset_by_id = {item.id: item for item in registry.datasets}
    collector_by_id = {str(item["id"]): item for item in registry.collectors}
    migration = migration_by_id.get(_OPTION_RAW_EVIDENCE_MIGRATION_ID)
    dataset = dataset_by_id.get(_OPTION_RAW_EVIDENCE_DATASET_ID)
    if (
        registry.source_sha256 != _OPTION_RAW_EVIDENCE_REGISTRY_SOURCE_SHA256
        or hashlib.sha256(payload).hexdigest()
        != _OPTION_RAW_EVIDENCE_REGISTRY_SOURCE_SHA256
        or migration is None
        or migration.store != "market"
        or migration.ordinal != 11
        or migration.resource
        != "quant_data/migrations/market/0011_option_raw_evidence.sql"
        or migration.sha256
        != "f6a4685963e1eddffb7fd92944e19e4bbeda0c89bc196d85a2308f1f408d8129"
        or migration.dependencies != ("market:0010_stage10_market_history",)
        or registry.store("market").migration_order[-1:]
        != (_OPTION_RAW_EVIDENCE_MIGRATION_ID,)
        or dataset is None
        or dataset.store != "market"
        or dataset.layer != "evidence"
        or dataset.relations != _OPTION_RAW_EVIDENCE_RELATIONS
        or set(dataset.collector_ids) != set(_ALPACA_OPTION_COLLECTOR_IDS)
        or dataset.tool_ids
        or dataset.dashboard_ids
        or any(
            collector_id not in collector_by_id
            or _OPTION_RAW_EVIDENCE_DATASET_ID
            not in collector_by_id[collector_id]["output_datasets"]
            or "raw_response_content_sha256"
            not in collector_by_id[collector_id]["semantic_identity"]["includes"]
            for collector_id in _ALPACA_OPTION_COLLECTOR_IDS
        )
    ):
        raise RegistryError("Current raw-option evidence identity drifted")

    migrations = tuple(
        item
        for item in registry.migrations
        if item.id != _OPTION_RAW_EVIDENCE_MIGRATION_ID
    )
    datasets = tuple(
        item
        for item in registry.datasets
        if item.id != _OPTION_RAW_EVIDENCE_DATASET_ID
    )
    collectors: list[Mapping[str, Any]] = []
    for item in registry.collectors:
        projected = copy.deepcopy(dict(item))
        if projected["id"] in _ALPACA_OPTION_COLLECTOR_IDS:
            projected["output_datasets"] = [
                dataset_id
                for dataset_id in projected["output_datasets"]
                if dataset_id != _OPTION_RAW_EVIDENCE_DATASET_ID
            ]
            projected["semantic_identity"]["includes"] = [
                field
                for field in projected["semantic_identity"]["includes"]
                if field != "raw_response_content_sha256"
            ]
        collectors.append(projected)
    stores = tuple(
        replace(
            store,
            migration_order=tuple(
                migration_id
                for migration_id in store.migration_order
                if migration_id != _OPTION_RAW_EVIDENCE_MIGRATION_ID
            ),
        )
        for store in registry.stores
    )

    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.64.0"
    raw["migrations"] = [
        item
        for item in raw["migrations"]
        if item["id"] != _OPTION_RAW_EVIDENCE_MIGRATION_ID
    ]
    raw["datasets"] = [
        item
        for item in raw["datasets"]
        if item["id"] != _OPTION_RAW_EVIDENCE_DATASET_ID
    ]
    for store in raw["stores"]:
        store["migration_order"] = [
            migration_id
            for migration_id in store["migration_order"]
            if migration_id != _OPTION_RAW_EVIDENCE_MIGRATION_ID
        ]
    for collector in raw["collectors"]:
        if collector["id"] in _ALPACA_OPTION_COLLECTOR_IDS:
            collector["output_datasets"] = [
                dataset_id
                for dataset_id in collector["output_datasets"]
                if dataset_id != _OPTION_RAW_EVIDENCE_DATASET_ID
            ]
            collector["semantic_identity"]["includes"] = [
                field
                for field in collector["semantic_identity"]["includes"]
                if field != "raw_response_content_sha256"
            ]
    projected_payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(projected_payload).hexdigest()
        != _CURRENT_MULTI_SOURCE_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError("Raw-option evidence projection drifted")
    return replace(
        registry,
        registry_version="2.64.0",
        stores=stores,
        migrations=migrations,
        datasets=datasets,
        collectors=tuple(collectors),
        raw=raw,
        source_sha256=_CURRENT_MULTI_SOURCE_REGISTRY_SOURCE_SHA256,
    )


def multi_source_current_news_registry_profile(
    registry: Registry,
) -> Registry:
    """Project multi-source current news 2.64 back to exact registry 2.63."""

    registry = option_raw_evidence_registry_profile(registry)
    version = (registry.schema_version, registry.registry_version)
    if version not in {("1.9.0", "2.64.0"), ("1.9.0", "2.63.0")}:
        return registry
    payload = (
        json.dumps(registry.raw, ensure_ascii=True, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    predecessor_catalog = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.23.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _CURRENT_NEWS_CATALOG_SOURCE_SHA256,
    }
    predecessor_policies = _pre_registry_264_policies()
    predecessor_inventory = tuple(
        (
            str(policy["tool"]),
            tuple(str(item["version"]) for item in policy["variants"]),
        )
        for policy in predecessor_policies
    )
    if version == ("1.9.0", "2.63.0"):
        if (
            registry.source_sha256 != _CURRENT_NEWS_REGISTRY_SOURCE_SHA256
            or hashlib.sha256(payload).hexdigest()
            != _CURRENT_NEWS_REGISTRY_SOURCE_SHA256
            or _tool_policy_variant_inventory(registry)
            != predecessor_inventory
            or registry.raw.get("tool_version_schema_catalog")
            != predecessor_catalog
        ):
            raise RegistryError(
                "Registry 2.63 multi-source-news predecessor identity drifted"
            )
        return registry

    current_catalog = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.24.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _CURRENT_MULTI_SOURCE_CATALOG_SOURCE_SHA256,
    }
    current_inventory = tuple(
        (
            str(policy["tool"]),
            tuple(str(item["version"]) for item in policy["variants"]),
        )
        for policy in _pre_registry_266_policies()
    )
    migration_by_id = {item.id: item for item in registry.migrations}
    dataset_by_id = {item.id: item for item in registry.datasets}
    collector_by_id = {str(item["id"]): item for item in registry.collectors}
    migration = migration_by_id.get(_CURRENT_MULTI_SOURCE_MIGRATION_ID)
    legacy_migration = migration_by_id.get(_LEGACY_FMP_NEWS_MIGRATION_ID)
    legacy_dataset = dataset_by_id.get(_LEGACY_FMP_NEWS_DATASET_ID)
    collector = collector_by_id.get(_CURRENT_MULTI_SOURCE_COLLECTOR_ID)
    if (
        registry.source_sha256
        != _CURRENT_MULTI_SOURCE_REGISTRY_SOURCE_SHA256
        or hashlib.sha256(payload).hexdigest()
        != _CURRENT_MULTI_SOURCE_REGISTRY_SOURCE_SHA256
        or _tool_policy_variant_inventory(registry) != current_inventory
        or registry.raw.get("tool_version_schema_catalog") != current_catalog
        or migration is None
        or migration.store != "news"
        or migration.ordinal != 7
        or migration.resource
        != "quant_data/migrations/news/0007_current_multi_source.sql"
        or migration.sha256
        != "df58936c73045ba8a382bf0a24743db5e314246e0f81d8943872b6d3e6c010c3"
        or migration.dependencies
        != (_FMP_STOCK_LATEST_CURRENT_MIGRATION_ID,)
        or legacy_migration is None
        or legacy_migration.store != "news"
        or legacy_migration.ordinal != 8
        or legacy_migration.resource
        != "quant_data/migrations/news/0008_adopt_fmp_news_legacy.sql"
        or legacy_migration.sha256
        != "ea5302726758ab2bb987a525c3094885e016e4596689d26c8290808523f8327f"
        or legacy_migration.dependencies
        != (_CURRENT_MULTI_SOURCE_MIGRATION_ID,)
        or legacy_dataset is None
        or legacy_dataset.relations
        != _LEGACY_FMP_NEWS_DATASET_RELATIONS[_LEGACY_FMP_NEWS_DATASET_ID]
        or not _CURRENT_MULTI_SOURCE_DATASET_IDS.issubset(dataset_by_id)
        or {
            dataset_id: dataset_by_id[dataset_id].relations
            for dataset_id in _CURRENT_MULTI_SOURCE_DATASET_IDS
        }
        != dict(_CURRENT_MULTI_SOURCE_DATASET_RELATIONS)
        or any(
            dataset_by_id[dataset_id].store != "news"
            or dataset_by_id[dataset_id].collector_ids
            != (_CURRENT_MULTI_SOURCE_COLLECTOR_ID,)
            or dataset_by_id[dataset_id].tool_ids != ("news.search",)
            for dataset_id in _CURRENT_MULTI_SOURCE_DATASET_IDS
        )
        or collector is None
        or collector["handler"] != _CURRENT_MULTI_SOURCE_COLLECTOR_ID
        or legacy_dataset.store != "news"
        or legacy_dataset.layer != "evidence"
        or legacy_dataset.collector_ids
        or legacy_dataset.tool_ids
        or legacy_dataset.dashboard_ids
        or legacy_dataset.export_ids
        or collector["network"] is not True
        or collector["input_datasets"] != ["market.stage10.instruments"]
        or set(collector["output_datasets"])
        != set(_CURRENT_MULTI_SOURCE_DATASET_IDS)
        or collector["schedule_eligibility"] != {"mode": "manual_only"}
        or collector["configuration_env"]
        != ["FMP_API_KEY", "ALPACA_API_KEY", "ALPACA_API_SECRET"]
        or legacy_dataset.active
        or registry.store("news").migration_order[-2:]
        != (
            _CURRENT_MULTI_SOURCE_MIGRATION_ID,
            _LEGACY_FMP_NEWS_MIGRATION_ID,
        )
    ):
        raise RegistryError("Current multi-source-news identity drifted")

    retained_policies: list[Mapping[str, Any]] = []
    removed: list[tuple[str, str]] = []
    for policy in registry.tool_version_policies:
        projected_policy = copy.deepcopy(dict(policy))
        if projected_policy["tool"] == "news.search":
            projected_variants = []
            for variant in projected_policy["variants"]:
                if variant["version"] == "2.1.0":
                    removed.append(("news.search", "2.1.0"))
                else:
                    projected_variants.append(variant)
            projected_policy["variants"] = projected_variants
        retained_policies.append(projected_policy)
    if (
        tuple(removed) != (("news.search", "2.1.0"),)
        or _tool_policy_variant_inventory(
            replace(
                registry,
                tool_version_policies=tuple(retained_policies),
            )
        )
        != predecessor_inventory
    ):
        raise RegistryError("Current multi-source-news tool inventory drifted")

    release_migration_ids = frozenset(
        (_CURRENT_MULTI_SOURCE_MIGRATION_ID, _LEGACY_FMP_NEWS_MIGRATION_ID)
    )
    release_dataset_ids = frozenset(
        (*_CURRENT_MULTI_SOURCE_DATASET_IDS, _LEGACY_FMP_NEWS_DATASET_ID)
    )

    migrations = tuple(
        item
        for item in registry.migrations
        if item.id not in release_migration_ids
    )
    datasets = tuple(
        item
        for item in registry.datasets
        if item.id not in release_dataset_ids
    )
    collectors = tuple(
        item
        for item in registry.collectors
        if item["id"] != _CURRENT_MULTI_SOURCE_COLLECTOR_ID
    )
    stores = tuple(
        replace(
            store,
            migration_order=tuple(
                migration_id
                for migration_id in store.migration_order
                if migration_id not in release_migration_ids
            ),
        )
        for store in registry.stores
    )

    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.63.0"
    raw["tool_version_schema_catalog"] = predecessor_catalog
    raw["tool_versions"] = copy.deepcopy(retained_policies)
    raw["migrations"] = [
        item
        for item in raw["migrations"]
        if item["id"] not in release_migration_ids
    ]
    raw["datasets"] = [
        item
        for item in raw["datasets"]
        if item["id"] not in release_dataset_ids
    ]
    raw["collectors"] = [
        item
        for item in raw["collectors"]
        if item["id"] != _CURRENT_MULTI_SOURCE_COLLECTOR_ID
    ]
    for store in raw["stores"]:
        store["migration_order"] = [
            migration_id
            for migration_id in store["migration_order"]
            if migration_id not in release_migration_ids
        ]
    projected_payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(projected_payload).hexdigest()
        != _CURRENT_NEWS_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError("Multi-source current-news projection drifted")
    return replace(
        registry,
        registry_version="2.63.0",
        stores=stores,
        migrations=migrations,
        datasets=datasets,
        collectors=collectors,
        tool_version_policies=tuple(retained_policies),
        raw=raw,
        source_sha256=_CURRENT_NEWS_REGISTRY_SOURCE_SHA256,
    )


def current_news_registry_profile(registry: Registry) -> Registry:
    """Project current-news registry 2.63 back to exact registry 2.62."""

    registry = multi_source_current_news_registry_profile(registry)

    version = (registry.schema_version, registry.registry_version)
    if version not in {("1.9.0", "2.63.0"), ("1.9.0", "2.62.0")}:
        return registry
    payload = (
        json.dumps(registry.raw, ensure_ascii=True, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    predecessor_catalog = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.22.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _TECHNICAL_INDICATORS_V27_CATALOG_SOURCE_SHA256,
    }
    predecessor_inventory = tuple(
        (
            str(policy["tool"]),
            tuple(str(item["version"]) for item in policy["variants"]),
        )
        for policy in _pre_registry_267_policies()
        if policy["tool"] not in VERSIONED_NEWS_TOOLS
    )
    if version == ("1.9.0", "2.62.0"):
        if (
            registry.source_sha256
            != _TECHNICAL_INDICATORS_V27_REGISTRY_SOURCE_SHA256
            or hashlib.sha256(payload).hexdigest()
            != _TECHNICAL_INDICATORS_V27_REGISTRY_SOURCE_SHA256
            or _tool_policy_variant_inventory(registry)
            != predecessor_inventory
            or registry.raw.get("tool_version_schema_catalog")
            != predecessor_catalog
        ):
            raise RegistryError(
                "Registry 2.62 current-news predecessor identity drifted"
            )
        return registry

    current_catalog = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.23.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _CURRENT_NEWS_CATALOG_SOURCE_SHA256,
    }
    current_inventory = tuple(
        (
            str(policy["tool"]),
            tuple(str(item["version"]) for item in policy["variants"]),
        )
        for policy in _pre_registry_264_policies()
    )
    if (
        registry.source_sha256 != _CURRENT_NEWS_REGISTRY_SOURCE_SHA256
        or hashlib.sha256(payload).hexdigest()
        != _CURRENT_NEWS_REGISTRY_SOURCE_SHA256
        or _tool_policy_variant_inventory(registry) != current_inventory
        or registry.raw.get("tool_version_schema_catalog") != current_catalog
    ):
        raise RegistryError("Current-news registry 2.63 identity drifted")

    added_migrations = tuple(
        item
        for item in registry.migrations
        if item.id == _FMP_STOCK_LATEST_CURRENT_MIGRATION_ID
    )
    added_datasets = tuple(
        item
        for item in registry.datasets
        if item.id in _FMP_STOCK_LATEST_CURRENT_DATASET_IDS
    )
    added_collectors = tuple(
        item
        for item in registry.collectors
        if item["id"] == _FMP_STOCK_LATEST_CURRENT_COLLECTOR_ID
    )
    if (
        len(added_migrations) != 1
        or added_migrations[0].store != "news"
        or added_migrations[0].ordinal != 6
        or added_migrations[0].resource
        != "quant_data/migrations/news/0006_fmp_stock_latest_current.sql"
        or added_migrations[0].sha256
        != "bf8757bcc7679d1bd57408978eed996c9f4dad9c89339a52ca8adbeedbf83d30"
        or added_migrations[0].dependencies
        != ("news:0005_fmp_stock_latest",)
        or added_migrations[0].reconstruction_state != "fixture_validated"
        or len(added_datasets) != 2
        or {
            item.id: item.relations for item in added_datasets
        }
        != dict(_FMP_STOCK_LATEST_CURRENT_DATASET_RELATIONS)
        or any(
            item.store != "news"
            or item.collector_ids
            != (_FMP_STOCK_LATEST_CURRENT_COLLECTOR_ID,)
            or item.tool_ids != ("news.search",)
            for item in added_datasets
        )
        or len(added_collectors) != 1
        or registry.store("news").migration_order[-1:]
        != (_FMP_STOCK_LATEST_CURRENT_MIGRATION_ID,)
    ):
        raise RegistryError("Current-news structural inventory drifted")

    retained_policies = tuple(
        policy
        for policy in registry.tool_version_policies
        if policy["tool"] not in VERSIONED_NEWS_TOOLS
    )
    if (
        len(registry.tool_version_policies) - len(retained_policies) != 1
        or _tool_policy_variant_inventory(
            replace(registry, tool_version_policies=retained_policies)
        )
        != predecessor_inventory
    ):
        raise RegistryError("Current-news tool inventory drifted")

    migrations = tuple(
        item
        for item in registry.migrations
        if item.id != _FMP_STOCK_LATEST_CURRENT_MIGRATION_ID
    )
    datasets = tuple(
        item
        for item in registry.datasets
        if item.id not in _FMP_STOCK_LATEST_CURRENT_DATASET_IDS
    )
    collectors = tuple(
        item
        for item in registry.collectors
        if item["id"] != _FMP_STOCK_LATEST_CURRENT_COLLECTOR_ID
    )
    stores = tuple(
        replace(
            store,
            migration_order=tuple(
                migration_id
                for migration_id in store.migration_order
                if migration_id != _FMP_STOCK_LATEST_CURRENT_MIGRATION_ID
            ),
        )
        for store in registry.stores
    )

    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.62.0"
    raw["tool_version_schema_catalog"] = predecessor_catalog
    raw["tool_versions"] = [
        item
        for item in raw["tool_versions"]
        if item["tool"] not in VERSIONED_NEWS_TOOLS
    ]
    raw["migrations"] = [
        item
        for item in raw["migrations"]
        if item["id"] != _FMP_STOCK_LATEST_CURRENT_MIGRATION_ID
    ]
    raw["datasets"] = [
        item
        for item in raw["datasets"]
        if item["id"] not in _FMP_STOCK_LATEST_CURRENT_DATASET_IDS
    ]
    raw["collectors"] = [
        item
        for item in raw["collectors"]
        if item["id"] != _FMP_STOCK_LATEST_CURRENT_COLLECTOR_ID
    ]
    for store in raw["stores"]:
        store["migration_order"] = [
            migration_id
            for migration_id in store["migration_order"]
            if migration_id != _FMP_STOCK_LATEST_CURRENT_MIGRATION_ID
        ]
    projected_payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(projected_payload).hexdigest()
        != _TECHNICAL_INDICATORS_V27_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError("Current-news registry projection drifted")
    return replace(
        registry,
        registry_version="2.62.0",
        stores=stores,
        migrations=migrations,
        datasets=datasets,
        collectors=collectors,
        tool_version_policies=retained_policies,
        raw=raw,
        source_sha256=_TECHNICAL_INDICATORS_V27_REGISTRY_SOURCE_SHA256,
    )


def technical_indicators_v27_registry_profile(
    registry: Registry,
) -> Registry:
    """Project the rolling regression v2.7 variant back to registry 2.61."""
    registry = current_news_registry_profile(registry)

    version = (registry.schema_version, registry.registry_version)
    if version not in {("1.9.0", "2.62.0"), ("1.9.0", "2.61.0")}:
        return registry
    payload = (
        json.dumps(registry.raw, ensure_ascii=True, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    predecessor_catalog = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.21.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _TECHNICAL_INDICATORS_V26_CATALOG_SOURCE_SHA256,
    }
    predecessor_inventory = tuple(
        (
            str(policy["tool"]),
            tuple(str(item["version"]) for item in policy["variants"]),
        )
        for policy in _pre_registry_262_policies()
    )
    if version == ("1.9.0", "2.61.0"):
        if (
            registry.source_sha256
            != _TECHNICAL_INDICATORS_V26_REGISTRY_SOURCE_SHA256
            or hashlib.sha256(payload).hexdigest()
            != _TECHNICAL_INDICATORS_V26_REGISTRY_SOURCE_SHA256
            or _tool_policy_variant_inventory(registry)
            != predecessor_inventory
            or registry.raw.get("tool_version_schema_catalog")
            != predecessor_catalog
        ):
            raise RegistryError(
                "Registry 2.61 rolling-regression predecessor identity drifted"
            )
        return registry

    current_catalog = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.22.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _TECHNICAL_INDICATORS_V27_CATALOG_SOURCE_SHA256,
    }
    current_inventory = tuple(
        (
            str(policy["tool"]),
            tuple(str(item["version"]) for item in policy["variants"]),
        )
        for policy in _pre_registry_267_policies()
        if policy["tool"] not in VERSIONED_NEWS_TOOLS
    )
    if (
        registry.source_sha256
        != _TECHNICAL_INDICATORS_V27_REGISTRY_SOURCE_SHA256
        or hashlib.sha256(payload).hexdigest()
        != _TECHNICAL_INDICATORS_V27_REGISTRY_SOURCE_SHA256
        or _tool_policy_variant_inventory(registry) != current_inventory
        or registry.raw.get("tool_version_schema_catalog") != current_catalog
    ):
        raise RegistryError("Current rolling-regression v2.7 identity drifted")

    retained_policies: list[Mapping[str, Any]] = []
    removed: list[tuple[str, str]] = []
    for policy in registry.tool_version_policies:
        projected_policy = copy.deepcopy(dict(policy))
        if projected_policy["tool"] == "market.technical_indicators":
            projected_variants = []
            for variant in projected_policy["variants"]:
                if variant["version"] == "2.7.0":
                    removed.append(
                        (str(projected_policy["tool"]), "2.7.0")
                    )
                else:
                    projected_variants.append(variant)
            projected_policy["variants"] = projected_variants
        retained_policies.append(projected_policy)
    if tuple(removed) != (
        ("market.technical_indicators", "2.7.0"),
    ):
        raise RegistryError("Rolling regression v2.7 inventory drifted")

    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.61.0"
    raw["tool_versions"] = copy.deepcopy(retained_policies)
    raw["tool_version_schema_catalog"] = predecessor_catalog
    projected_payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(projected_payload).hexdigest()
        != _TECHNICAL_INDICATORS_V26_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError("Rolling regression v2.7 projection drifted")
    return replace(
        registry,
        registry_version="2.61.0",
        tool_version_policies=tuple(retained_policies),
        raw=raw,
        source_sha256=_TECHNICAL_INDICATORS_V26_REGISTRY_SOURCE_SHA256,
    )


def technical_indicators_v26_registry_profile(
    registry: Registry,
) -> Registry:
    """Project the Parabolic SAR v2.6 variant back to exact registry 2.60."""
    registry = technical_indicators_v27_registry_profile(registry)

    version = (registry.schema_version, registry.registry_version)
    if version not in {("1.9.0", "2.61.0"), ("1.9.0", "2.60.0")}:
        return registry
    payload = (
        json.dumps(registry.raw, ensure_ascii=True, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    predecessor_catalog = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.20.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _TECHNICAL_INDICATORS_V25_CATALOG_SOURCE_SHA256,
    }
    predecessor_inventory = tuple(
        (
            str(policy["tool"]),
            tuple(str(item["version"]) for item in policy["variants"]),
        )
        for policy in _pre_registry_261_policies()
    )
    if version == ("1.9.0", "2.60.0"):
        if (
            registry.source_sha256
            != _TECHNICAL_INDICATORS_V25_REGISTRY_SOURCE_SHA256
            or hashlib.sha256(payload).hexdigest()
            != _TECHNICAL_INDICATORS_V25_REGISTRY_SOURCE_SHA256
            or _tool_policy_variant_inventory(registry)
            != predecessor_inventory
            or registry.raw.get("tool_version_schema_catalog")
            != predecessor_catalog
        ):
            raise RegistryError(
                "Registry 2.60 Parabolic SAR predecessor identity drifted"
            )
        return registry

    current_catalog = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.21.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _TECHNICAL_INDICATORS_V26_CATALOG_SOURCE_SHA256,
    }
    current_inventory = tuple(
        (
            str(policy["tool"]),
            tuple(str(item["version"]) for item in policy["variants"]),
        )
        for policy in _pre_registry_262_policies()
    )
    if (
        registry.source_sha256
        != _TECHNICAL_INDICATORS_V26_REGISTRY_SOURCE_SHA256
        or hashlib.sha256(payload).hexdigest()
        != _TECHNICAL_INDICATORS_V26_REGISTRY_SOURCE_SHA256
        or _tool_policy_variant_inventory(registry) != current_inventory
        or registry.raw.get("tool_version_schema_catalog") != current_catalog
    ):
        raise RegistryError("Current Parabolic SAR v2.6 identity drifted")

    retained_policies: list[Mapping[str, Any]] = []
    removed: list[tuple[str, str]] = []
    for policy in registry.tool_version_policies:
        projected_policy = copy.deepcopy(dict(policy))
        if projected_policy["tool"] == "market.technical_indicators":
            projected_variants = []
            for variant in projected_policy["variants"]:
                if variant["version"] == "2.6.0":
                    removed.append(
                        (str(projected_policy["tool"]), "2.6.0")
                    )
                else:
                    projected_variants.append(variant)
            projected_policy["variants"] = projected_variants
        retained_policies.append(projected_policy)
    if tuple(removed) != (
        ("market.technical_indicators", "2.6.0"),
    ):
        raise RegistryError("Parabolic SAR v2.6 inventory drifted")

    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.60.0"
    raw["tool_versions"] = copy.deepcopy(retained_policies)
    raw["tool_version_schema_catalog"] = predecessor_catalog
    projected_payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(projected_payload).hexdigest()
        != _TECHNICAL_INDICATORS_V25_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError("Parabolic SAR v2.6 projection drifted")
    return replace(
        registry,
        registry_version="2.60.0",
        tool_version_policies=tuple(retained_policies),
        raw=raw,
        source_sha256=_TECHNICAL_INDICATORS_V25_REGISTRY_SOURCE_SHA256,
    )


def technical_indicators_v25_registry_profile(
    registry: Registry,
) -> Registry:
    """Project the WaveTrend v2.5 variant back to exact registry 2.59."""

    registry = technical_indicators_v26_registry_profile(registry)
    version = (registry.schema_version, registry.registry_version)
    if version not in {("1.9.0", "2.60.0"), ("1.9.0", "2.59.0")}:
        return registry
    payload = (
        json.dumps(registry.raw, ensure_ascii=True, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    predecessor_catalog = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.19.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _REGISTRY_259_ADDITIVE_SUCCESSORS_CATALOG_SOURCE_SHA256,
    }
    predecessor_inventory = tuple(
        (
            str(policy["tool"]),
            tuple(str(item["version"]) for item in policy["variants"]),
        )
        for policy in _pre_registry_260_policies()
    )
    if version == ("1.9.0", "2.59.0"):
        if (
            registry.source_sha256
            != _REGISTRY_259_ADDITIVE_SUCCESSORS_SOURCE_SHA256
            or hashlib.sha256(payload).hexdigest()
            != _REGISTRY_259_ADDITIVE_SUCCESSORS_SOURCE_SHA256
            or _tool_policy_variant_inventory(registry)
            != predecessor_inventory
            or registry.raw.get("tool_version_schema_catalog")
            != predecessor_catalog
        ):
            raise RegistryError(
                "Registry 2.59 WaveTrend predecessor identity drifted"
            )
        return registry

    current_catalog = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.20.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _TECHNICAL_INDICATORS_V25_CATALOG_SOURCE_SHA256,
    }
    current_inventory = tuple(
        (
            str(policy["tool"]),
            tuple(str(item["version"]) for item in policy["variants"]),
        )
        for policy in _pre_registry_261_policies()
    )
    if (
        registry.source_sha256
        != _TECHNICAL_INDICATORS_V25_REGISTRY_SOURCE_SHA256
        or hashlib.sha256(payload).hexdigest()
        != _TECHNICAL_INDICATORS_V25_REGISTRY_SOURCE_SHA256
        or _tool_policy_variant_inventory(registry) != current_inventory
        or registry.raw.get("tool_version_schema_catalog") != current_catalog
    ):
        raise RegistryError("Current WaveTrend v2.5 identity drifted")

    retained_policies: list[Mapping[str, Any]] = []
    removed: list[tuple[str, str]] = []
    for policy in registry.tool_version_policies:
        projected_policy = copy.deepcopy(dict(policy))
        if projected_policy["tool"] == "market.technical_indicators":
            projected_variants = []
            for variant in projected_policy["variants"]:
                if variant["version"] == "2.5.0":
                    removed.append(
                        (str(projected_policy["tool"]), "2.5.0")
                    )
                else:
                    projected_variants.append(variant)
            projected_policy["variants"] = projected_variants
        retained_policies.append(projected_policy)
    if tuple(removed) != (
        ("market.technical_indicators", "2.5.0"),
    ):
        raise RegistryError("WaveTrend v2.5 inventory drifted")

    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.59.0"
    raw["tool_versions"] = copy.deepcopy(retained_policies)
    raw["tool_version_schema_catalog"] = predecessor_catalog
    projected_payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(projected_payload).hexdigest()
        != _REGISTRY_259_ADDITIVE_SUCCESSORS_SOURCE_SHA256
    ):
        raise RegistryError("WaveTrend v2.5 projection drifted")
    return replace(
        registry,
        registry_version="2.59.0",
        tool_version_policies=tuple(retained_policies),
        raw=raw,
        source_sha256=_REGISTRY_259_ADDITIVE_SUCCESSORS_SOURCE_SHA256,
    )


def registry_259_additive_successors_profile(
    registry: Registry,
) -> Registry:
    """Project the five reviewed registry 2.59 additions back to 2.58."""

    registry = technical_indicators_v25_registry_profile(registry)
    version = (registry.schema_version, registry.registry_version)
    if version not in {("1.9.0", "2.59.0"), ("1.9.0", "2.58.0")}:
        return registry
    payload = (
        json.dumps(registry.raw, ensure_ascii=True, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    predecessor_catalog = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.18.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _TECHNICAL_INDICATORS_V23_CATALOG_SOURCE_SHA256,
    }
    predecessor_inventory = tuple(
        (
            str(policy["tool"]),
            tuple(str(item["version"]) for item in policy["variants"]),
        )
        for policy in _pre_registry_259_policies()
    )
    if version == ("1.9.0", "2.58.0"):
        if (
            registry.source_sha256
            != _TECHNICAL_INDICATORS_V23_REGISTRY_SOURCE_SHA256
            or hashlib.sha256(payload).hexdigest()
            != _TECHNICAL_INDICATORS_V23_REGISTRY_SOURCE_SHA256
            or _tool_policy_variant_inventory(registry)
            != predecessor_inventory
            or registry.raw.get("tool_version_schema_catalog")
            != predecessor_catalog
        ):
            raise RegistryError(
                "Registry 2.58 additive-successor predecessor identity drifted"
            )
        return registry

    current_catalog = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.19.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _REGISTRY_259_ADDITIVE_SUCCESSORS_CATALOG_SOURCE_SHA256,
    }
    current_inventory = tuple(
        (
            str(policy["tool"]),
            tuple(str(item["version"]) for item in policy["variants"]),
        )
        for policy in _pre_registry_260_policies()
    )
    if (
        registry.source_sha256
        != _REGISTRY_259_ADDITIVE_SUCCESSORS_SOURCE_SHA256
        or hashlib.sha256(payload).hexdigest()
        != _REGISTRY_259_ADDITIVE_SUCCESSORS_SOURCE_SHA256
        or _tool_policy_variant_inventory(registry) != current_inventory
        or registry.raw.get("tool_version_schema_catalog") != current_catalog
    ):
        raise RegistryError("Current registry 2.59 successor identity drifted")

    retained_policies: list[Mapping[str, Any]] = []
    removed: list[tuple[str, str]] = []
    for policy in registry.tool_version_policies:
        projected_policy = copy.deepcopy(dict(policy))
        projected_variants = []
        for variant in projected_policy["variants"]:
            key = (
                str(projected_policy["tool"]),
                str(variant["version"]),
            )
            if key in _REGISTRY_259_ADDITIVE_VARIANTS:
                removed.append(key)
            else:
                projected_variants.append(variant)
        projected_policy["variants"] = projected_variants
        retained_policies.append(projected_policy)
    if (
        len(removed) != len(_REGISTRY_259_ADDITIVE_VARIANTS)
        or frozenset(removed) != _REGISTRY_259_ADDITIVE_VARIANTS
    ):
        raise RegistryError("Registry 2.59 additive inventory drifted")

    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.58.0"
    raw["tool_versions"] = copy.deepcopy(retained_policies)
    raw["tool_version_schema_catalog"] = predecessor_catalog
    projected_payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(projected_payload).hexdigest()
        != _TECHNICAL_INDICATORS_V23_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError("Registry 2.59 additive projection drifted")
    return replace(
        registry,
        registry_version="2.58.0",
        tool_version_policies=tuple(retained_policies),
        raw=raw,
        source_sha256=_TECHNICAL_INDICATORS_V23_REGISTRY_SOURCE_SHA256,
    )



def technical_indicators_v23_registry_profile(
    registry: Registry,
) -> Registry:
    """Project the KDJ v2.3 variant back to exact registry 2.57."""

    registry = registry_259_additive_successors_profile(registry)
    version = (registry.schema_version, registry.registry_version)
    if version not in {("1.9.0", "2.58.0"), ("1.9.0", "2.57.0")}:
        return registry
    payload = (
        json.dumps(registry.raw, ensure_ascii=True, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    predecessor_catalog = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.17.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _INVESTMENT_ANALYSIS_V2_CATALOG_SOURCE_SHA256,
    }
    indicator_inventory = tuple(
        item
        for item in _tool_policy_variant_inventory(registry)
        if item[0] == "market.technical_indicators"
    )
    predecessor_inventory = tuple(
        (
            str(policy["tool"]),
            tuple(str(item["version"]) for item in policy["variants"]),
        )
        for policy in _pre_technical_indicators_v23_policies()
    )
    if version == ("1.9.0", "2.57.0"):
        if (
            registry.source_sha256
            != _INVESTMENT_ANALYSIS_V2_REGISTRY_SOURCE_SHA256
            or hashlib.sha256(payload).hexdigest()
            != _INVESTMENT_ANALYSIS_V2_REGISTRY_SOURCE_SHA256
            or _tool_policy_variant_inventory(registry)
            != predecessor_inventory
            or indicator_inventory
            != (
                (
                    "market.technical_indicators",
                    ("2.0.0", "2.1.0", "2.2.0"),
                ),
            )
            or registry.raw.get("tool_version_schema_catalog")
            != predecessor_catalog
        ):
            raise RegistryError(
                "Registry 2.57 KDJ predecessor identity drifted"
            )
        return registry

    current_catalog = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.18.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _TECHNICAL_INDICATORS_V23_CATALOG_SOURCE_SHA256,
    }
    current_inventory = tuple(
        (
            str(policy["tool"]),
            tuple(str(item["version"]) for item in policy["variants"]),
        )
        for policy in _pre_registry_259_policies()
    )
    if (
        registry.source_sha256
        != _TECHNICAL_INDICATORS_V23_REGISTRY_SOURCE_SHA256
        or hashlib.sha256(payload).hexdigest()
        != _TECHNICAL_INDICATORS_V23_REGISTRY_SOURCE_SHA256
        or _tool_policy_variant_inventory(registry) != current_inventory
        or indicator_inventory
        != (
            (
                "market.technical_indicators",
                ("2.0.0", "2.1.0", "2.2.0", "2.3.0"),
            ),
        )
        or registry.raw.get("tool_version_schema_catalog") != current_catalog
    ):
        raise RegistryError("Current technical-indicator v2.3 identity drifted")

    retained_policies: list[Mapping[str, Any]] = []
    removed: list[tuple[str, str]] = []
    for policy in registry.tool_version_policies:
        projected_policy = copy.deepcopy(dict(policy))
        if projected_policy["tool"] == "market.technical_indicators":
            projected_variants = []
            for variant in projected_policy["variants"]:
                if variant["version"] == "2.3.0":
                    removed.append(
                        (str(projected_policy["tool"]), "2.3.0")
                    )
                else:
                    projected_variants.append(variant)
            projected_policy["variants"] = projected_variants
        retained_policies.append(projected_policy)
    if tuple(removed) != (
        ("market.technical_indicators", "2.3.0"),
    ):
        raise RegistryError("KDJ v2.3 inventory drifted")

    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.57.0"
    raw["tool_versions"] = copy.deepcopy(retained_policies)
    raw["tool_version_schema_catalog"] = predecessor_catalog
    projected_payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(projected_payload).hexdigest()
        != _INVESTMENT_ANALYSIS_V2_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError("Technical-indicator v2.3 projection drifted")
    return replace(
        registry,
        registry_version="2.57.0",
        tool_version_policies=tuple(retained_policies),
        raw=raw,
        source_sha256=_INVESTMENT_ANALYSIS_V2_REGISTRY_SOURCE_SHA256,
    )


def investment_analysis_v2_registry_profile(registry: Registry) -> Registry:
    """Project the investment-analysis successors back to exact registry 2.56."""

    registry = technical_indicators_v23_registry_profile(registry)

    version = (registry.schema_version, registry.registry_version)
    if version not in {("1.9.0", "2.57.0"), ("1.9.0", "2.56.0")}:
        return registry
    payload = (
        json.dumps(registry.raw, ensure_ascii=True, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    predecessor_catalog = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.16.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _TECHNICAL_INDICATORS_V22_CATALOG_SOURCE_SHA256,
    }
    predecessor_inventory = tuple(
        (
            str(policy["tool"]),
            tuple(str(item["version"]) for item in policy["variants"]),
        )
        for policy in _pre_investment_analysis_policies()
    )
    if version == ("1.9.0", "2.56.0"):
        if (
            registry.source_sha256
            != _TECHNICAL_INDICATORS_V22_REGISTRY_SOURCE_SHA256
            or hashlib.sha256(payload).hexdigest()
            != _TECHNICAL_INDICATORS_V22_REGISTRY_SOURCE_SHA256
            or _tool_policy_variant_inventory(registry)
            != predecessor_inventory
            or registry.raw.get("tool_version_schema_catalog")
            != predecessor_catalog
        ):
            raise RegistryError(
                "Registry 2.56 investment-analysis predecessor identity drifted"
            )
        return registry

    current_catalog = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.17.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _INVESTMENT_ANALYSIS_V2_CATALOG_SOURCE_SHA256,
    }
    current_inventory = tuple(
        (
            str(policy["tool"]),
            tuple(str(item["version"]) for item in policy["variants"]),
        )
        for policy in _pre_technical_indicators_v23_policies()
    )
    if (
        registry.source_sha256
        != _INVESTMENT_ANALYSIS_V2_REGISTRY_SOURCE_SHA256
        or hashlib.sha256(payload).hexdigest()
        != _INVESTMENT_ANALYSIS_V2_REGISTRY_SOURCE_SHA256
        or _tool_policy_variant_inventory(registry) != current_inventory
        or registry.raw.get("tool_version_schema_catalog") != current_catalog
    ):
        raise RegistryError(
            "Current investment-analysis registry identity drifted"
        )

    new_tools = frozenset(VERSIONED_INVESTMENT_ANALYSIS_TOOLS)
    removed = tuple(
        str(policy["tool"])
        for policy in registry.tool_version_policies
        if policy["tool"] in new_tools
    )
    if removed != VERSIONED_INVESTMENT_ANALYSIS_TOOLS:
        raise RegistryError("Investment-analysis successor inventory drifted")
    retained_policies = tuple(
        copy.deepcopy(dict(policy))
        for policy in registry.tool_version_policies
        if policy["tool"] not in new_tools
    )

    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.56.0"
    raw["tool_versions"] = copy.deepcopy(list(retained_policies))
    raw["tool_version_schema_catalog"] = predecessor_catalog
    by_dataset: dict[str, list[str]] = {
        str(item["id"]): [] for item in raw["datasets"]
    }
    for declaration in raw["tools"]:
        for dataset_id in declaration["datasets"]:
            by_dataset[dataset_id].append(str(declaration["id"]))
    for policy in retained_policies:
        for variant in policy["variants"]:
            for dataset_id in variant["datasets"]:
                tool_id = str(variant["id"])
                if tool_id not in by_dataset[dataset_id]:
                    by_dataset[dataset_id].append(tool_id)
    for dataset in raw["datasets"]:
        dataset["tool_ids"] = by_dataset[str(dataset["id"])]

    projected_payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(projected_payload).hexdigest()
        != _TECHNICAL_INDICATORS_V22_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError("Investment-analysis registry projection drifted")
    projected_tool_ids = {
        str(item["id"]): tuple(str(value) for value in item["tool_ids"])
        for item in raw["datasets"]
    }
    projected_datasets = tuple(
        replace(item, tool_ids=projected_tool_ids[item.id])
        for item in registry.datasets
    )
    return replace(
        registry,
        registry_version="2.56.0",
        datasets=projected_datasets,
        tool_version_policies=retained_policies,
        raw=raw,
        source_sha256=_TECHNICAL_INDICATORS_V22_REGISTRY_SOURCE_SHA256,
    )


def technical_indicators_v22_registry_profile(
    registry: Registry,
) -> Registry:
    """Project the swing-forecast v2.2 variant back to exact registry 2.55."""

    registry = investment_analysis_v2_registry_profile(registry)

    version = (registry.schema_version, registry.registry_version)
    if version not in {("1.9.0", "2.56.0"), ("1.9.0", "2.55.0")}:
        return registry
    payload = (
        json.dumps(registry.raw, ensure_ascii=True, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    predecessor_catalog = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.15.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _TECHNICAL_INDICATORS_V21_CATALOG_SOURCE_SHA256,
    }
    indicator_inventory = tuple(
        item
        for item in _tool_policy_variant_inventory(registry)
        if item[0] == "market.technical_indicators"
    )
    if version == ("1.9.0", "2.55.0"):
        if (
            registry.source_sha256
            != _TECHNICAL_INDICATORS_V21_REGISTRY_SOURCE_SHA256
            or hashlib.sha256(payload).hexdigest()
            != _TECHNICAL_INDICATORS_V21_REGISTRY_SOURCE_SHA256
            or indicator_inventory
            != (
                (
                    "market.technical_indicators",
                    ("2.0.0", "2.1.0"),
                ),
            )
            or registry.raw.get("tool_version_schema_catalog")
            != predecessor_catalog
        ):
            raise RegistryError(
                "Registry 2.55 technical-indicator predecessor identity drifted"
            )
        return registry

    current_catalog = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.16.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _TECHNICAL_INDICATORS_V22_CATALOG_SOURCE_SHA256,
    }
    current_inventory = tuple(
        (
            str(policy["tool"]),
            tuple(str(item["version"]) for item in policy["variants"]),
        )
        for policy in _pre_investment_analysis_policies()
    )
    if (
        registry.source_sha256
        != _TECHNICAL_INDICATORS_V22_REGISTRY_SOURCE_SHA256
        or hashlib.sha256(payload).hexdigest()
        != _TECHNICAL_INDICATORS_V22_REGISTRY_SOURCE_SHA256
        or _tool_policy_variant_inventory(registry) != current_inventory
        or indicator_inventory
        != (
            (
                "market.technical_indicators",
                ("2.0.0", "2.1.0", "2.2.0"),
            ),
        )
        or registry.raw.get("tool_version_schema_catalog") != current_catalog
    ):
        raise RegistryError(
            "Current technical-indicator v2.2 registry identity drifted"
        )

    retained_policies: list[Mapping[str, Any]] = []
    removed: list[tuple[str, str]] = []
    for policy in registry.tool_version_policies:
        projected_policy = copy.deepcopy(dict(policy))
        if projected_policy["tool"] == "market.technical_indicators":
            projected_variants = []
            for variant in projected_policy["variants"]:
                if variant["version"] == "2.2.0":
                    removed.append(
                        (str(projected_policy["tool"]), "2.2.0")
                    )
                else:
                    projected_variants.append(variant)
            projected_policy["variants"] = projected_variants
        retained_policies.append(projected_policy)
    if tuple(removed) != (
        ("market.technical_indicators", "2.2.0"),
    ):
        raise RegistryError("Swing-structure forecast v2.2 inventory drifted")

    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.55.0"
    raw["tool_versions"] = copy.deepcopy(retained_policies)
    raw["tool_version_schema_catalog"] = predecessor_catalog
    projected_payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(projected_payload).hexdigest()
        != _TECHNICAL_INDICATORS_V21_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError(
            "Technical-indicator v2.2 registry projection drifted"
        )
    return replace(
        registry,
        registry_version="2.55.0",
        tool_version_policies=tuple(retained_policies),
        raw=raw,
        source_sha256=_TECHNICAL_INDICATORS_V21_REGISTRY_SOURCE_SHA256,
    )


def technical_indicators_v21_registry_profile(
    registry: Registry,
) -> Registry:
    """Project the SuperTrend AI v2.1 variant back to exact registry 2.54."""

    registry = technical_indicators_v22_registry_profile(registry)
    version = (registry.schema_version, registry.registry_version)
    if version not in {("1.9.0", "2.55.0"), ("1.9.0", "2.54.0")}:
        return registry
    payload = (
        json.dumps(registry.raw, ensure_ascii=True, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    predecessor_catalog = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.14.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _PLACEHOLDER_SUCCESSOR_CATALOG_SOURCE_SHA256,
    }
    indicator_inventory = tuple(
        item
        for item in _tool_policy_variant_inventory(registry)
        if item[0] == "market.technical_indicators"
    )
    if version == ("1.9.0", "2.54.0"):
        if (
            registry.source_sha256
            != _PLACEHOLDER_SUCCESSOR_REGISTRY_SOURCE_SHA256
            or hashlib.sha256(payload).hexdigest()
            != _PLACEHOLDER_SUCCESSOR_REGISTRY_SOURCE_SHA256
            or indicator_inventory
            != (("market.technical_indicators", ("2.0.0",)),)
            or registry.raw.get("tool_version_schema_catalog")
            != predecessor_catalog
        ):
            raise RegistryError(
                "Registry 2.54 technical-indicator predecessor identity drifted"
            )
        return registry

    current_catalog = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.15.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _TECHNICAL_INDICATORS_V21_CATALOG_SOURCE_SHA256,
    }
    current_inventory = tuple(
        (
            str(policy["tool"]),
            tuple(
                str(item["version"])
                for item in policy["variants"]
                if not (
                    policy["tool"] == "market.technical_indicators"
                    and item["version"] == "2.2.0"
                )
            ),
        )
        for policy in _pre_investment_analysis_policies()
    )
    if (
        registry.source_sha256
        != _TECHNICAL_INDICATORS_V21_REGISTRY_SOURCE_SHA256
        or hashlib.sha256(payload).hexdigest()
        != _TECHNICAL_INDICATORS_V21_REGISTRY_SOURCE_SHA256
        or _tool_policy_variant_inventory(registry) != current_inventory
        or indicator_inventory
        != (("market.technical_indicators", ("2.0.0", "2.1.0")),)
        or registry.raw.get("tool_version_schema_catalog") != current_catalog
    ):
        raise RegistryError(
            "Current technical-indicator v2.1 registry identity drifted"
        )

    retained_policies: list[Mapping[str, Any]] = []
    removed: list[tuple[str, str]] = []
    for policy in registry.tool_version_policies:
        projected_policy = copy.deepcopy(dict(policy))
        if projected_policy["tool"] == "market.technical_indicators":
            projected_variants = []
            for variant in projected_policy["variants"]:
                if variant["version"] == "2.1.0":
                    removed.append(
                        (str(projected_policy["tool"]), "2.1.0")
                    )
                else:
                    projected_variants.append(variant)
            projected_policy["variants"] = projected_variants
        retained_policies.append(projected_policy)
    if tuple(removed) != (("market.technical_indicators", "2.1.0"),):
        raise RegistryError("SuperTrend AI v2.1 inventory drifted")

    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.54.0"
    raw["tool_versions"] = copy.deepcopy(retained_policies)
    raw["tool_version_schema_catalog"] = predecessor_catalog
    projected_payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(projected_payload).hexdigest()
        != _PLACEHOLDER_SUCCESSOR_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError(
            "Technical-indicator v2.1 registry projection drifted"
        )
    return replace(
        registry,
        registry_version="2.54.0",
        tool_version_policies=tuple(retained_policies),
        raw=raw,
        source_sha256=_PLACEHOLDER_SUCCESSOR_REGISTRY_SOURCE_SHA256,
    )


def placeholder_successor_registry_profile(
    registry: Registry,
) -> Registry:
    """Project the first data-backed placeholder successors to exact 2.53."""

    registry = technical_indicators_v21_registry_profile(registry)
    version = (registry.schema_version, registry.registry_version)
    if version not in {("1.9.0", "2.54.0"), ("1.9.0", "2.53.0")}:
        return registry
    payload = (
        json.dumps(registry.raw, ensure_ascii=True, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    predecessor_catalog = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.13.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _COMPANY_FILING_PAGINATION_CATALOG_SOURCE_SHA256,
    }
    if version == ("1.9.0", "2.53.0"):
        if (
            registry.source_sha256
            != _COMPANY_FILING_PAGINATION_REGISTRY_SOURCE_SHA256
            or hashlib.sha256(payload).hexdigest()
            != _COMPANY_FILING_PAGINATION_REGISTRY_SOURCE_SHA256
            or any(
                policy["tool"] in _PLACEHOLDER_SUCCESSOR_VERSIONED_TOOL_IDS
                for policy in registry.tool_version_policies
            )
            or registry.raw.get("tool_version_schema_catalog")
            != predecessor_catalog
        ):
            raise RegistryError(
                "Registry 2.53 placeholder-successor predecessor identity drifted"
            )
        return registry

    current_catalog = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.14.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _PLACEHOLDER_SUCCESSOR_CATALOG_SOURCE_SHA256,
    }
    current_inventory = tuple(
        (
            str(policy["tool"]),
            tuple(
                str(item["version"])
                for item in policy["variants"]
                if not (
                    policy["tool"] == "market.technical_indicators"
                    and item["version"] in {"2.1.0", "2.2.0"}
                )
            ),
        )
        for policy in _pre_investment_analysis_policies()
    )
    successor_names = (
        *VERSIONED_COMPANY_SHARE_COUNT_TOOLS,
        *VERSIONED_MARKET_INSTRUMENT_SEARCH_TOOLS,
    )
    successor_inventory = tuple(
        (name, ("2.0.0",)) for name in successor_names
    )
    observed_successor_inventory = tuple(
        item
        for item in _tool_policy_variant_inventory(registry)
        if item[0] in _PLACEHOLDER_SUCCESSOR_VERSIONED_TOOL_IDS
    )
    if (
        registry.source_sha256
        != _PLACEHOLDER_SUCCESSOR_REGISTRY_SOURCE_SHA256
        or hashlib.sha256(payload).hexdigest()
        != _PLACEHOLDER_SUCCESSOR_REGISTRY_SOURCE_SHA256
        or _tool_policy_variant_inventory(registry) != current_inventory
        or observed_successor_inventory != successor_inventory
        or registry.raw.get("tool_version_schema_catalog") != current_catalog
    ):
        raise RegistryError("Current placeholder-successor registry identity drifted")

    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.53.0"
    raw["tool_versions"] = [
        item
        for item in raw["tool_versions"]
        if item["tool"] not in _PLACEHOLDER_SUCCESSOR_VERSIONED_TOOL_IDS
    ]
    raw["tool_version_schema_catalog"] = predecessor_catalog
    for dataset in raw["datasets"]:
        if dataset["id"] == "market.stage10.instruments":
            dataset["tool_ids"] = [
                tool_id
                for tool_id in dataset["tool_ids"]
                if tool_id != "market.search_instruments"
            ]
    projected_payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(projected_payload).hexdigest()
        != _COMPANY_FILING_PAGINATION_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError("Placeholder-successor registry projection drifted")
    policies = tuple(
        item
        for item in registry.tool_version_policies
        if str(item["tool"]) not in _PLACEHOLDER_SUCCESSOR_VERSIONED_TOOL_IDS
    )
    datasets = tuple(
        replace(
            item,
            tool_ids=tuple(
                tool_id
                for tool_id in item.tool_ids
                if tool_id != "market.search_instruments"
            ),
        )
        if item.id == "market.stage10.instruments"
        else item
        for item in registry.datasets
    )
    return replace(
        registry,
        registry_version="2.53.0",
        datasets=datasets,
        tool_version_policies=policies,
        raw=raw,
        source_sha256=_COMPANY_FILING_PAGINATION_REGISTRY_SOURCE_SHA256,
    )


def company_filing_pagination_registry_profile(
    registry: Registry,
) -> Registry:
    """Project filing pagination back to the exact registry 2.52."""

    registry = placeholder_successor_registry_profile(registry)

    version = (registry.schema_version, registry.registry_version)
    if version not in {("1.9.0", "2.53.0"), ("1.9.0", "2.52.0")}:
        return registry
    payload = (
        json.dumps(registry.raw, ensure_ascii=True, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    predecessor_catalog = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.12.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _STAGE10_RESEARCH_ANALYTICS_CATALOG_SOURCE_SHA256,
    }
    if version == ("1.9.0", "2.52.0"):
        if (
            registry.source_sha256
            != _STAGE10_RESEARCH_ANALYTICS_REGISTRY_SOURCE_SHA256
            or hashlib.sha256(payload).hexdigest()
            != _STAGE10_RESEARCH_ANALYTICS_REGISTRY_SOURCE_SHA256
            or any(
                policy["tool"] in VERSIONED_COMPANY_FILING_TOOLS
                for policy in registry.tool_version_policies
            )
            or registry.raw.get("tool_version_schema_catalog")
            != predecessor_catalog
        ):
            raise RegistryError(
                "Registry 2.52 filing-pagination predecessor identity drifted"
            )
        return registry

    current_catalog = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.13.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _COMPANY_FILING_PAGINATION_CATALOG_SOURCE_SHA256,
    }
    current_inventory = tuple(
        (
            str(policy["tool"]),
            tuple(
                str(item["version"])
                for item in policy["variants"]
                if not (
                    policy["tool"] == "market.technical_indicators"
                    and item["version"] in {"2.1.0", "2.2.0"}
                )
            ),
        )
        for policy in _pre_investment_analysis_policies()
        if policy["tool"] not in _PLACEHOLDER_SUCCESSOR_VERSIONED_TOOL_IDS
    )
    filing_inventory = tuple(
        (name, ("2.0.0",)) for name in VERSIONED_COMPANY_FILING_TOOLS
    )
    observed_filing_inventory = tuple(
        item
        for item in _tool_policy_variant_inventory(registry)
        if item[0] in VERSIONED_COMPANY_FILING_TOOLS
    )
    if (
        registry.source_sha256
        != _COMPANY_FILING_PAGINATION_REGISTRY_SOURCE_SHA256
        or hashlib.sha256(payload).hexdigest()
        != _COMPANY_FILING_PAGINATION_REGISTRY_SOURCE_SHA256
        or _tool_policy_variant_inventory(registry) != current_inventory
        or observed_filing_inventory != filing_inventory
        or registry.raw.get("tool_version_schema_catalog") != current_catalog
    ):
        raise RegistryError("Current filing-pagination registry identity drifted")

    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.52.0"
    raw["tool_versions"] = [
        item
        for item in raw["tool_versions"]
        if item["tool"] not in VERSIONED_COMPANY_FILING_TOOLS
    ]
    raw["tool_version_schema_catalog"] = predecessor_catalog
    projected_payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(projected_payload).hexdigest()
        != _STAGE10_RESEARCH_ANALYTICS_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError("Filing-pagination registry projection drifted")
    policies = tuple(
        item
        for item in registry.tool_version_policies
        if str(item["tool"]) not in VERSIONED_COMPANY_FILING_TOOLS
    )
    return replace(
        registry,
        registry_version="2.52.0",
        tool_version_policies=policies,
        raw=raw,
        source_sha256=_STAGE10_RESEARCH_ANALYTICS_REGISTRY_SOURCE_SHA256,
    )

def stage10_research_analytics_registry_profile(registry: Registry) -> Registry:
    """Project the seven Stage 10 analytical successors back to exact 2.51."""

    registry = company_filing_pagination_registry_profile(registry)

    version = (registry.schema_version, registry.registry_version)
    if version not in {("1.9.0", "2.52.0"), ("1.9.0", "2.51.0")}:
        return registry
    payload = (
        json.dumps(registry.raw, ensure_ascii=True, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    predecessor_catalog = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.11.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _TECHNICAL_INDICATORS_V2_CATALOG_SOURCE_SHA256,
    }
    if version == ("1.9.0", "2.51.0"):
        if (
            registry.source_sha256
            != _IWM_ETF_DAILY_HISTORY_BACKFILL_REGISTRY_SOURCE_SHA256
            or hashlib.sha256(payload).hexdigest()
            != _IWM_ETF_DAILY_HISTORY_BACKFILL_REGISTRY_SOURCE_SHA256
            or any(
                policy["tool"] in VERSIONED_RESEARCH_ANALYTIC_TOOLS
                for policy in registry.tool_version_policies
            )
            or registry.raw.get("tool_version_schema_catalog")
            != predecessor_catalog
        ):
            raise RegistryError(
                "Registry 2.51 research-analytics predecessor identity drifted"
            )
        return registry

    current_catalog = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.12.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _STAGE10_RESEARCH_ANALYTICS_CATALOG_SOURCE_SHA256,
    }
    current_inventory = tuple(
        (
            str(policy["tool"]),
            tuple(
                str(item["version"])
                for item in policy["variants"]
                if not (
                    policy["tool"] == "market.technical_indicators"
                    and item["version"] in {"2.1.0", "2.2.0"}
                )
            ),
        )
        for policy in _pre_investment_analysis_policies()
        if policy["tool"] not in VERSIONED_COMPANY_FILING_TOOLS
        if policy["tool"] not in _PLACEHOLDER_SUCCESSOR_VERSIONED_TOOL_IDS
    )
    research_inventory = tuple(
        (name, ("2.0.0",)) for name in VERSIONED_RESEARCH_ANALYTIC_TOOLS
    )
    observed_research_inventory = tuple(
        item
        for item in _tool_policy_variant_inventory(registry)
        if item[0] in VERSIONED_RESEARCH_ANALYTIC_TOOLS
    )
    if (
        registry.source_sha256
        != _STAGE10_RESEARCH_ANALYTICS_REGISTRY_SOURCE_SHA256
        or hashlib.sha256(payload).hexdigest()
        != _STAGE10_RESEARCH_ANALYTICS_REGISTRY_SOURCE_SHA256
        or _tool_policy_variant_inventory(registry) != current_inventory
        or observed_research_inventory != research_inventory
        or registry.raw.get("tool_version_schema_catalog") != current_catalog
    ):
        raise RegistryError("Current research-analytics registry identity drifted")

    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.51.0"
    raw["tool_versions"] = [
        item
        for item in raw["tool_versions"]
        if item["tool"] not in VERSIONED_RESEARCH_ANALYTIC_TOOLS
    ]
    raw["tool_version_schema_catalog"] = predecessor_catalog
    projected_payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(projected_payload).hexdigest()
        != _IWM_ETF_DAILY_HISTORY_BACKFILL_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError("Research-analytics registry projection drifted")
    policies = tuple(
        item
        for item in registry.tool_version_policies
        if str(item["tool"]) not in VERSIONED_RESEARCH_ANALYTIC_TOOLS
    )
    return replace(
        registry,
        registry_version="2.51.0",
        tool_version_policies=policies,
        raw=raw,
        source_sha256=_IWM_ETF_DAILY_HISTORY_BACKFILL_REGISTRY_SOURCE_SHA256,
    )


def fmp_iwm_etf_daily_history_backfill_registry_profile(
    registry: Registry,
) -> Registry:
    """Project the one-shot IWM missing-interval collector back to exact 2.50."""

    registry = stage10_research_analytics_registry_profile(registry)

    version = (registry.schema_version, registry.registry_version)
    if version not in {("1.9.0", "2.51.0"), ("1.9.0", "2.50.0")}:
        return registry
    payload = (
        json.dumps(registry.raw, ensure_ascii=True, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    dataset_by_id = {item.id: item for item in registry.datasets}
    collector_by_id = {str(item["id"]): item for item in registry.collectors}
    if version == ("1.9.0", "2.50.0"):
        if (
            registry.source_sha256 != _IWM_ETF_DAILY_HISTORY_REGISTRY_SOURCE_SHA256
            or hashlib.sha256(payload).hexdigest()
            != _IWM_ETF_DAILY_HISTORY_REGISTRY_SOURCE_SHA256
            or _IWM_ETF_DAILY_HISTORY_BACKFILL_COLLECTOR_ID in collector_by_id
            or any(
                _IWM_ETF_DAILY_HISTORY_BACKFILL_COLLECTOR_ID
                in dataset_by_id[dataset_id].collector_ids
                for dataset_id in _IWM_ETF_DAILY_HISTORY_BACKFILL_DATASET_IDS
            )
        ):
            raise RegistryError("Registry 2.50 IWM backfill predecessor identity drifted")
        return registry

    collector = collector_by_id.get(
        _IWM_ETF_DAILY_HISTORY_BACKFILL_COLLECTOR_ID
    )
    if (
        registry.source_sha256
        != _IWM_ETF_DAILY_HISTORY_BACKFILL_REGISTRY_SOURCE_SHA256
        or hashlib.sha256(payload).hexdigest()
        != _IWM_ETF_DAILY_HISTORY_BACKFILL_REGISTRY_SOURCE_SHA256
        or len(registry.collectors) != 56
        or collector is None
        or collector["version"] != "1.0.0"
        or collector["handler"]
        != "market.fmp_iwm_etf_daily_history_backfill"
        or collector["network"] is not True
        or collector["input_datasets"]
        != ["market.stage10.instruments", "market.stage10.universes"]
        or collector["output_datasets"]
        != list(_IWM_ETF_DAILY_HISTORY_BACKFILL_DATASET_IDS)
        or collector["semantic_identity"]
        != {
            "excludes": [
                "api_key",
                "captured_at",
                "http_headers",
                "source_row_order",
            ],
            "includes": [
                "iwm_scope_manifest_sha256",
                "backfill_scope_manifest_sha256",
                "request_scope",
                "normalization_version",
                "normalized_complete_backfill_sha256",
            ],
        }
        or collector["mutation_policy"]
        != {
            "mode": "append_missing_price_versions",
            "unchanged": "zero_persistent_writes",
        }
        or collector["workload_bounds"]
        != {
            "max_bytes": 16_777_216,
            "max_requests": 1,
            "max_rows": 30_000,
            "max_seconds": 45,
        }
        or collector["retry_policy"]
        != {
            "backoff": "none_single_attempt",
            "honor_retry_after": False,
            "max_attempts": 1,
            "transient_classes": [],
        }
        or collector["configuration_env"] != ["FMP_API_KEY"]
        or collector["physical_locks"] != "derived_from_output_store_paths"
        or collector["schedule_eligibility"] != {"mode": "manual_only"}
        or any(
            dataset_by_id[dataset_id].collector_ids.count(
                _IWM_ETF_DAILY_HISTORY_BACKFILL_COLLECTOR_ID
            )
            != 1
            for dataset_id in _IWM_ETF_DAILY_HISTORY_BACKFILL_DATASET_IDS
        )
        or any(
            step.collector_id == _IWM_ETF_DAILY_HISTORY_BACKFILL_COLLECTOR_ID
            for job in registry.jobs
            for step in job.steps
        )
    ):
        raise RegistryError("Current IWM backfill registry identity drifted")

    projected_collectors = tuple(
        item
        for item in registry.collectors
        if str(item["id"]) != _IWM_ETF_DAILY_HISTORY_BACKFILL_COLLECTOR_ID
    )
    projected_datasets = tuple(
        replace(
            item,
            collector_ids=tuple(
                collector_id
                for collector_id in item.collector_ids
                if collector_id
                != _IWM_ETF_DAILY_HISTORY_BACKFILL_COLLECTOR_ID
            ),
        )
        for item in registry.datasets
    )
    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.50.0"
    raw["collectors"] = [
        item
        for item in raw["collectors"]
        if item["id"] != _IWM_ETF_DAILY_HISTORY_BACKFILL_COLLECTOR_ID
    ]
    raw["datasets"] = [
        {
            **item,
            "collector_ids": [
                collector_id
                for collector_id in item["collector_ids"]
                if collector_id
                != _IWM_ETF_DAILY_HISTORY_BACKFILL_COLLECTOR_ID
            ],
        }
        for item in raw["datasets"]
    ]
    projected_payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(projected_payload).hexdigest()
        != _IWM_ETF_DAILY_HISTORY_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError("IWM backfill registry projection drifted")
    return replace(
        registry,
        registry_version="2.50.0",
        datasets=projected_datasets,
        collectors=projected_collectors,
        raw=raw,
        source_sha256=_IWM_ETF_DAILY_HISTORY_REGISTRY_SOURCE_SHA256,
    )



def fmp_iwm_etf_daily_history_registry_profile(registry: Registry) -> Registry:
    """Project the additive IWM history collector back to exact 2.49."""

    registry = fmp_iwm_etf_daily_history_backfill_registry_profile(registry)

    version = (registry.schema_version, registry.registry_version)
    if version not in {("1.9.0", "2.50.0"), ("1.9.0", "2.49.0")}:
        return registry
    payload = (
        json.dumps(registry.raw, ensure_ascii=True, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    dataset_by_id = {item.id: item for item in registry.datasets}
    collector_by_id = {str(item["id"]): item for item in registry.collectors}
    if version == ("1.9.0", "2.49.0"):
        if (
            registry.source_sha256 != _ALPACA_ETF_OPTION_REGISTRY_SOURCE_SHA256
            or hashlib.sha256(payload).hexdigest()
            != _ALPACA_ETF_OPTION_REGISTRY_SOURCE_SHA256
            or _IWM_ETF_DAILY_HISTORY_COLLECTOR_ID in collector_by_id
            or any(
                _IWM_ETF_DAILY_HISTORY_COLLECTOR_ID
                in dataset_by_id[dataset_id].collector_ids
                for dataset_id in _IWM_ETF_DAILY_HISTORY_DATASET_IDS
            )
        ):
            raise RegistryError("Registry 2.49 IWM history predecessor identity drifted")
        return registry

    collector = collector_by_id.get(_IWM_ETF_DAILY_HISTORY_COLLECTOR_ID)
    if (
        registry.source_sha256 != _IWM_ETF_DAILY_HISTORY_REGISTRY_SOURCE_SHA256
        or hashlib.sha256(payload).hexdigest()
        != _IWM_ETF_DAILY_HISTORY_REGISTRY_SOURCE_SHA256
        or len(registry.collectors) != 55
        or collector is None
        or collector["version"] != "1.0.0"
        or collector["handler"] != "market.fmp_iwm_etf_daily_history"
        or collector["network"] is not True
        or collector["input_datasets"]
        != ["market.stage10.instruments", "market.stage10.universes"]
        or collector["output_datasets"] != list(_IWM_ETF_DAILY_HISTORY_DATASET_IDS)
        or collector["semantic_identity"]
        != {
            "excludes": [
                "api_key",
                "captured_at",
                "http_headers",
                "source_row_order",
            ],
            "includes": [
                "scope_manifest_sha256",
                "base_scope_manifest_sha256",
                "successor_membership_sha256",
                "request_scope",
                "normalization_version",
                "normalized_complete_history_sha256",
            ],
        }
        or collector["mutation_policy"]
        != {
            "mode": "append_successor_universe_and_price_versions",
            "unchanged": "zero_persistent_writes",
        }
        or collector["workload_bounds"]
        != {
            "max_bytes": 16_777_216,
            "max_requests": 1,
            "max_rows": 30_000,
            "max_seconds": 45,
        }
        or collector["retry_policy"]
        != {
            "backoff": "none_single_attempt",
            "honor_retry_after": False,
            "max_attempts": 1,
            "transient_classes": [],
        }
        or collector["configuration_env"] != ["FMP_API_KEY"]
        or collector["physical_locks"] != "derived_from_output_store_paths"
        or collector["schedule_eligibility"] != {"mode": "manual_only"}
        or any(
            dataset_by_id[dataset_id].collector_ids.count(
                _IWM_ETF_DAILY_HISTORY_COLLECTOR_ID
            )
            != 1
            for dataset_id in _IWM_ETF_DAILY_HISTORY_DATASET_IDS
        )
        or any(
            step.collector_id == _IWM_ETF_DAILY_HISTORY_COLLECTOR_ID
            for job in registry.jobs
            for step in job.steps
        )
    ):
        raise RegistryError("Current IWM ETF daily-history registry identity drifted")

    projected_collectors = tuple(
        item
        for item in registry.collectors
        if str(item["id"]) != _IWM_ETF_DAILY_HISTORY_COLLECTOR_ID
    )
    projected_datasets = tuple(
        replace(
            item,
            collector_ids=tuple(
                collector_id
                for collector_id in item.collector_ids
                if collector_id != _IWM_ETF_DAILY_HISTORY_COLLECTOR_ID
            ),
        )
        for item in registry.datasets
    )
    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.49.0"
    raw["collectors"] = [
        item
        for item in raw["collectors"]
        if item["id"] != _IWM_ETF_DAILY_HISTORY_COLLECTOR_ID
    ]
    raw["datasets"] = [
        {
            **item,
            "collector_ids": [
                collector_id
                for collector_id in item["collector_ids"]
                if collector_id != _IWM_ETF_DAILY_HISTORY_COLLECTOR_ID
            ],
        }
        for item in raw["datasets"]
    ]
    projected_payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(projected_payload).hexdigest()
        != _ALPACA_ETF_OPTION_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError("IWM ETF daily-history registry projection drifted")
    return replace(
        registry,
        registry_version="2.49.0",
        datasets=projected_datasets,
        collectors=projected_collectors,
        raw=raw,
        source_sha256=_ALPACA_ETF_OPTION_REGISTRY_SOURCE_SHA256,
    )


def alpaca_etf_options_registry_profile(registry: Registry) -> Registry:
    """Project the broad manual Alpaca options collector back to exact 2.48."""

    registry = fmp_iwm_etf_daily_history_registry_profile(registry)
    version = (registry.schema_version, registry.registry_version)
    if version not in {("1.9.0", "2.49.0"), ("1.9.0", "2.48.0")}:
        return registry
    payload = (
        json.dumps(registry.raw, ensure_ascii=True, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    dataset_by_id = {item.id: item for item in registry.datasets}
    collector_by_id = {
        str(item["id"]): item for item in registry.collectors
    }
    if version == ("1.9.0", "2.48.0"):
        if (
            registry.source_sha256
            != _TECHNICAL_INDICATORS_V2_REGISTRY_SOURCE_SHA256
            or hashlib.sha256(payload).hexdigest()
            != _TECHNICAL_INDICATORS_V2_REGISTRY_SOURCE_SHA256
            or _ALPACA_ETF_OPTION_COLLECTOR_ID in collector_by_id
            or any(
                _ALPACA_ETF_OPTION_COLLECTOR_ID
                in dataset_by_id[dataset_id].collector_ids
                for dataset_id in _ALPACA_ETF_OPTION_DATASET_IDS
            )
        ):
            raise RegistryError(
                "Registry 2.48 Alpaca ETF options predecessor identity drifted"
            )
        return registry

    collector = collector_by_id.get(_ALPACA_ETF_OPTION_COLLECTOR_ID)
    if (
        registry.source_sha256 != _ALPACA_ETF_OPTION_REGISTRY_SOURCE_SHA256
        or hashlib.sha256(payload).hexdigest()
        != _ALPACA_ETF_OPTION_REGISTRY_SOURCE_SHA256
        or collector is None
        or collector["handler"] != "market.alpaca_etf_option_surface_grid"
        or collector["configuration_env"]
        != ["ALPACA_API_KEY", "ALPACA_API_SECRET"]
        or collector["output_datasets"]
        != list(_ALPACA_ETF_OPTION_DATASET_IDS)
        or collector["schedule_eligibility"] != {"mode": "manual_only"}
        or collector["workload_bounds"]
        != {
            "max_bytes": 268_435_456,
            "max_requests": 362,
            "max_rows": 900_016,
            "max_seconds": 900,
        }
        or any(
            dataset_by_id[dataset_id].collector_ids.count(
                _ALPACA_ETF_OPTION_COLLECTOR_ID
            )
            != 1
            for dataset_id in _ALPACA_ETF_OPTION_DATASET_IDS
        )
        or any(
            step.collector_id == _ALPACA_ETF_OPTION_COLLECTOR_ID
            for job in registry.jobs
            for step in job.steps
        )
    ):
        raise RegistryError("Current Alpaca ETF options registry identity drifted")

    projected_collectors = tuple(
        item
        for item in registry.collectors
        if str(item["id"]) != _ALPACA_ETF_OPTION_COLLECTOR_ID
    )
    projected_datasets = tuple(
        replace(
            item,
            collector_ids=tuple(
                collector_id
                for collector_id in item.collector_ids
                if collector_id != _ALPACA_ETF_OPTION_COLLECTOR_ID
            ),
        )
        for item in registry.datasets
    )
    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.48.0"
    raw["collectors"] = [
        item
        for item in raw["collectors"]
        if item["id"] != _ALPACA_ETF_OPTION_COLLECTOR_ID
    ]
    raw["datasets"] = [
        {
            **item,
            "collector_ids": [
                collector_id
                for collector_id in item["collector_ids"]
                if collector_id != _ALPACA_ETF_OPTION_COLLECTOR_ID
            ],
        }
        for item in raw["datasets"]
    ]
    projected_payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(projected_payload).hexdigest()
        != _TECHNICAL_INDICATORS_V2_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError("Alpaca ETF options registry projection drifted")
    return replace(
        registry,
        registry_version="2.48.0",
        datasets=projected_datasets,
        collectors=projected_collectors,
        raw=raw,
        source_sha256=_TECHNICAL_INDICATORS_V2_REGISTRY_SOURCE_SHA256,
    )


def technical_indicators_v2_registry_profile(registry: Registry) -> Registry:
    """Project the technical-indicator increment back to exact registry 2.47."""

    registry = alpaca_etf_options_registry_profile(registry)
    version = (registry.schema_version, registry.registry_version)
    if version not in {("1.9.0", "2.48.0"), ("1.9.0", "2.47.0")}:
        return registry
    payload = (
        json.dumps(registry.raw, ensure_ascii=True, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    tool_names = tuple(str(item["id"]) for item in registry.tools)
    raw_dataset_tool_ids = {
        str(item["id"]): tuple(str(tool_id) for tool_id in item["tool_ids"])
        for item in registry.raw["datasets"]
    }
    dataset_tool_ids_match = (
        len(raw_dataset_tool_ids) == len(registry.datasets)
        and all(
            raw_dataset_tool_ids.get(item.id) == item.tool_ids
            for item in registry.datasets
        )
    )
    predecessor_policies = tuple(
        policy
        for policy in _pre_investment_analysis_policies()
        if policy["tool"] != "market.technical_indicators"
        and policy["tool"] not in VERSIONED_RESEARCH_ANALYTIC_TOOLS
        and policy["tool"] not in VERSIONED_COMPANY_FILING_TOOLS
        and policy["tool"] not in _PLACEHOLDER_SUCCESSOR_VERSIONED_TOOL_IDS
    )
    predecessor_inventory = tuple(
        (
            str(policy["tool"]),
            tuple(str(item["version"]) for item in policy["variants"]),
        )
        for policy in predecessor_policies
    )
    predecessor_catalog = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.10.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _ANALYTICS_FOUNDATION_CATALOG_SOURCE_SHA256,
    }
    if version == ("1.9.0", "2.47.0"):
        if (
            registry.source_sha256
            != _ANALYTICS_FOUNDATION_REGISTRY_SOURCE_SHA256
            or hashlib.sha256(payload).hexdigest()
            != _ANALYTICS_FOUNDATION_REGISTRY_SOURCE_SHA256
            or tool_names != _PRE_NEWS_RESEARCH_TOOL_NAMES
            or not dataset_tool_ids_match
            or _tool_policy_variant_inventory(registry)
            != predecessor_inventory
            or registry.raw.get("tool_version_schema_catalog")
            != predecessor_catalog
        ):
            raise RegistryError(
                "Registry 2.47 technical-indicator predecessor identity drifted"
            )
        return registry

    current_catalog = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.11.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _TECHNICAL_INDICATORS_V2_CATALOG_SOURCE_SHA256,
    }
    current_inventory = tuple(
        (
            str(policy["tool"]),
            tuple(
                str(item["version"])
                for item in policy["variants"]
                if not (
                    policy["tool"] == "market.technical_indicators"
                    and item["version"] in {"2.1.0", "2.2.0"}
                )
            ),
        )
        for policy in _pre_investment_analysis_policies()
        if policy["tool"] not in VERSIONED_RESEARCH_ANALYTIC_TOOLS
        if policy["tool"] not in VERSIONED_COMPANY_FILING_TOOLS
        if policy["tool"] not in _PLACEHOLDER_SUCCESSOR_VERSIONED_TOOL_IDS
    )
    if (
        registry.source_sha256
        != _TECHNICAL_INDICATORS_V2_REGISTRY_SOURCE_SHA256
        or hashlib.sha256(payload).hexdigest()
        != _TECHNICAL_INDICATORS_V2_REGISTRY_SOURCE_SHA256
        or tool_names != _PRE_NEWS_RESEARCH_TOOL_NAMES
        or not dataset_tool_ids_match
        or _tool_policy_variant_inventory(registry) != current_inventory
        or registry.raw.get("tool_version_schema_catalog") != current_catalog
    ):
        raise RegistryError(
            "Current technical-indicator registry identity drifted"
        )

    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.47.0"
    raw["tool_versions"] = [
        item
        for item in raw["tool_versions"]
        if item["tool"] != "market.technical_indicators"
    ]
    raw["tool_version_schema_catalog"] = predecessor_catalog
    projected_payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(projected_payload).hexdigest()
        != _ANALYTICS_FOUNDATION_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError("Technical-indicator registry projection drifted")
    policies = tuple(
        item
        for item in registry.tool_version_policies
        if str(item["tool"]) != "market.technical_indicators"
    )
    return replace(
        registry,
        registry_version="2.47.0",
        tool_version_policies=policies,
        raw=raw,
        source_sha256=_ANALYTICS_FOUNDATION_REGISTRY_SOURCE_SHA256,
    )


def analytics_foundation_registry_profile(registry: Registry) -> Registry:
    """Project the analytics foundation increment back to exact registry 2.46."""

    registry = technical_indicators_v2_registry_profile(registry)
    version = (registry.schema_version, registry.registry_version)
    if version not in {("1.9.0", "2.47.0"), ("1.9.0", "2.46.0")}:
        return registry
    payload = (
        json.dumps(registry.raw, ensure_ascii=True, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    tool_names = tuple(str(item["id"]) for item in registry.tools)
    raw_dataset_tool_ids = {
        str(item["id"]): tuple(str(tool_id) for tool_id in item["tool_ids"])
        for item in registry.raw["datasets"]
    }
    dataset_tool_ids_match = (
        len(raw_dataset_tool_ids) == len(registry.datasets)
        and all(
            raw_dataset_tool_ids.get(item.id) == item.tool_ids
            for item in registry.datasets
        )
    )
    predecessor_policies = tuple(
        policy
        for policy in _pre_investment_analysis_policies()
        if policy["tool"]
        not in {
            "data.quality_audit",
            "timeseries.transform",
            "market.technical_indicators",
        }
        and policy["tool"] not in VERSIONED_RESEARCH_ANALYTIC_TOOLS
        and policy["tool"] not in VERSIONED_COMPANY_FILING_TOOLS
        and policy["tool"] not in _PLACEHOLDER_SUCCESSOR_VERSIONED_TOOL_IDS
    )
    predecessor_policy_inventory = tuple(
        (
            str(policy["tool"]),
            tuple(str(item["version"]) for item in policy["variants"]),
        )
        for policy in predecessor_policies
    )
    catalog_246 = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.9.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _CANONICAL_ACCESS_CATALOG_SOURCE_SHA256,
    }
    if version == ("1.9.0", "2.46.0"):
        if (
            registry.source_sha256
            != _CANONICAL_ACCESS_REGISTRY_SOURCE_SHA256
            or hashlib.sha256(payload).hexdigest()
            != _CANONICAL_ACCESS_REGISTRY_SOURCE_SHA256
            or tool_names != _PRE_ANALYTICS_FOUNDATION_TOOL_NAMES
            or not dataset_tool_ids_match
            or _tool_policy_variant_inventory(registry)
            != predecessor_policy_inventory
            or registry.raw.get("tool_version_schema_catalog") != catalog_246
        ):
            raise RegistryError(
                "Registry 2.46 analytics-foundation predecessor identity drifted"
            )
        return registry

    current_catalog = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.10.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _ANALYTICS_FOUNDATION_CATALOG_SOURCE_SHA256,
    }
    current_policy_inventory = tuple(
        (
            str(policy["tool"]),
            tuple(str(item["version"]) for item in policy["variants"]),
        )
        for policy in _pre_investment_analysis_policies()
        if policy["tool"] != "market.technical_indicators"
        and policy["tool"] not in VERSIONED_RESEARCH_ANALYTIC_TOOLS
        and policy["tool"] not in VERSIONED_COMPANY_FILING_TOOLS
        and policy["tool"] not in _PLACEHOLDER_SUCCESSOR_VERSIONED_TOOL_IDS
    )
    if (
        registry.source_sha256
        != _ANALYTICS_FOUNDATION_REGISTRY_SOURCE_SHA256
        or hashlib.sha256(payload).hexdigest()
        != _ANALYTICS_FOUNDATION_REGISTRY_SOURCE_SHA256
        or tool_names != _PRE_NEWS_RESEARCH_TOOL_NAMES
        or not dataset_tool_ids_match
        or _tool_policy_variant_inventory(registry)
        != current_policy_inventory
        or registry.raw.get("tool_version_schema_catalog") != current_catalog
    ):
        raise RegistryError("Current analytics-foundation registry identity drifted")

    removed_tools = set(ADDITIVE_STAGE10_STATISTICS_TOOLS)
    removed_policies = {"data.quality_audit", "timeseries.transform"}
    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.46.0"
    raw["tools"] = [
        item for item in raw["tools"] if item["id"] not in removed_tools
    ]
    raw["tool_versions"] = [
        item
        for item in raw["tool_versions"]
        if item["tool"] not in removed_policies
    ]
    raw["tool_version_schema_catalog"] = catalog_246
    raw["presentation_order"]["tools"] = list(
        _PRE_ANALYTICS_FOUNDATION_TOOL_NAMES
    )

    tool_ids_by_dataset: dict[str, list[str]] = {
        item["id"]: [] for item in raw["datasets"]
    }
    for tool in raw["tools"]:
        for dataset_id in tool["datasets"]:
            tool_ids_by_dataset[dataset_id].append(tool["id"])
    for policy in raw["tool_versions"]:
        for variant in policy["variants"]:
            for dataset_id in variant["datasets"]:
                if variant["id"] not in tool_ids_by_dataset[dataset_id]:
                    tool_ids_by_dataset[dataset_id].append(variant["id"])
    raw["datasets"] = [
        {**item, "tool_ids": tool_ids_by_dataset[item["id"]]}
        for item in raw["datasets"]
    ]
    projected_payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(projected_payload).hexdigest()
        != _CANONICAL_ACCESS_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError("Analytics-foundation registry projection drifted")

    tools = tuple(
        item for item in registry.tools if str(item["id"]) not in removed_tools
    )
    policies = tuple(
        item
        for item in registry.tool_version_policies
        if str(item["tool"]) not in removed_policies
    )
    projected_datasets = tuple(
        replace(item, tool_ids=tuple(tool_ids_by_dataset[item.id]))
        for item in registry.datasets
    )
    return replace(
        registry,
        registry_version="2.46.0",
        datasets=projected_datasets,
        tools=tools,
        tool_version_policies=policies,
        raw=raw,
        source_sha256=_CANONICAL_ACCESS_REGISTRY_SOURCE_SHA256,
    )


def canonical_access_registry_profile(registry: Registry) -> Registry:
    """Project Step 1 canonical access back to exact registry 2.45."""

    registry = analytics_foundation_registry_profile(registry)
    version = (registry.schema_version, registry.registry_version)
    if version not in {("1.9.0", "2.46.0"), ("1.9.0", "2.45.0")}:
        return registry
    payload = (
        json.dumps(registry.raw, ensure_ascii=True, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    tool_names = tuple(str(item["id"]) for item in registry.tools)
    raw_dataset_tool_ids = {
        str(item["id"]): tuple(str(tool_id) for tool_id in item["tool_ids"])
        for item in registry.raw["datasets"]
    }
    dataset_tool_ids_match = (
        len(raw_dataset_tool_ids) == len(registry.datasets)
        and all(
            raw_dataset_tool_ids.get(item.id) == item.tool_ids
            for item in registry.datasets
        )
    )
    catalog_245 = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.8.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _MARKET_AVAILABLE_TICKER_V1_CATALOG_SOURCE_SHA256,
    }
    if version == ("1.9.0", "2.45.0"):
        if (
            registry.source_sha256
            != _SEC_MARKET_COMPANYFACTS_REGISTRY_SOURCE_SHA256
            or hashlib.sha256(payload).hexdigest()
            != _SEC_MARKET_COMPANYFACTS_REGISTRY_SOURCE_SHA256
            or tool_names != _PRE_CANONICAL_ACCESS_TOOL_NAMES
            or not dataset_tool_ids_match
            or _tool_policy_variant_inventory(registry) != _POLICY_VARIANTS_2_39
            or registry.raw.get("tool_version_schema_catalog") != catalog_245
        ):
            raise RegistryError(
                "Registry 2.45 canonical-access predecessor identity drifted"
            )
        return registry

    current_catalog = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.9.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _CANONICAL_ACCESS_CATALOG_SOURCE_SHA256,
    }
    if (
        registry.source_sha256 != _CANONICAL_ACCESS_REGISTRY_SOURCE_SHA256
        or hashlib.sha256(payload).hexdigest()
        != _CANONICAL_ACCESS_REGISTRY_SOURCE_SHA256
        or tool_names != _PRE_ANALYTICS_FOUNDATION_TOOL_NAMES
        or not dataset_tool_ids_match
        or _tool_policy_variant_inventory(registry)
        != tuple(
            (
                str(policy["tool"]),
                tuple(str(item["version"]) for item in policy["variants"]),
            )
            for policy in _pre_investment_analysis_policies()
            if policy["tool"]
            not in {
                "data.quality_audit",
                "timeseries.transform",
                "market.technical_indicators",
            }
            and policy["tool"] not in VERSIONED_RESEARCH_ANALYTIC_TOOLS
            and policy["tool"] not in VERSIONED_COMPANY_FILING_TOOLS
            and policy["tool"] not in _PLACEHOLDER_SUCCESSOR_VERSIONED_TOOL_IDS
        )
        or registry.raw.get("tool_version_schema_catalog") != current_catalog
    ):
        raise RegistryError("Current canonical-access registry identity drifted")

    removed_tools = {"macro.get_release_calendar", "market.get_volume_series"}
    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.45.0"
    raw["tools"] = [
        item for item in raw["tools"] if item["id"] not in removed_tools
    ]
    raw["tool_versions"] = [
        item
        for item in raw["tool_versions"]
        if item["tool"] not in VERSIONED_CANONICAL_MACRO_TOOLS
    ]
    raw["tool_version_schema_catalog"] = catalog_245
    raw["presentation_order"]["tools"] = list(
        _PRE_CANONICAL_ACCESS_TOOL_NAMES
    )

    tool_ids_by_dataset: dict[str, list[str]] = {
        item["id"]: [] for item in raw["datasets"]
    }
    for tool in raw["tools"]:
        for dataset_id in tool["datasets"]:
            tool_ids_by_dataset[dataset_id].append(tool["id"])
    for policy in raw["tool_versions"]:
        for variant in policy["variants"]:
            for dataset_id in variant["datasets"]:
                if variant["id"] not in tool_ids_by_dataset[dataset_id]:
                    tool_ids_by_dataset[dataset_id].append(variant["id"])
    raw["datasets"] = [
        {**item, "tool_ids": tool_ids_by_dataset[item["id"]]}
        for item in raw["datasets"]
    ]

    projected_payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(projected_payload).hexdigest()
        != _SEC_MARKET_COMPANYFACTS_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError("Canonical access registry projection drifted")

    tools = tuple(
        item for item in registry.tools if str(item["id"]) not in removed_tools
    )
    policies = tuple(
        item
        for item in registry.tool_version_policies
        if str(item["tool"]) not in VERSIONED_CANONICAL_MACRO_TOOLS
    )
    projected_datasets = tuple(
        replace(item, tool_ids=tuple(tool_ids_by_dataset[item.id]))
        for item in registry.datasets
    )
    return replace(
        registry,
        registry_version="2.45.0",
        datasets=projected_datasets,
        tools=tools,
        tool_version_policies=policies,
        raw=raw,
        source_sha256=_SEC_MARKET_COMPANYFACTS_REGISTRY_SOURCE_SHA256,
    )


def sec_market_companyfacts_registry_profile(registry: Registry) -> Registry:
    """Project the additive market-universe SEC collector back to exact 2.44."""

    registry = canonical_access_registry_profile(registry)
    version = (registry.schema_version, registry.registry_version)
    if version not in {("1.9.0", "2.45.0"), ("1.9.0", "2.44.0")}:
        return registry
    payload = (
        json.dumps(registry.raw, ensure_ascii=True, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    dataset_by_id = {item.id: item for item in registry.datasets}
    collector_by_id = {str(item["id"]): item for item in registry.collectors}
    if version == ("1.9.0", "2.44.0"):
        if (
            registry.source_sha256
            != _SEC_AAPL_COMPANYFACTS_REGISTRY_SOURCE_SHA256
            or hashlib.sha256(payload).hexdigest()
            != _SEC_AAPL_COMPANYFACTS_REGISTRY_SOURCE_SHA256
            or _SEC_MARKET_COMPANYFACTS_COLLECTOR_ID in collector_by_id
            or any(
                _SEC_MARKET_COMPANYFACTS_COLLECTOR_ID
                in dataset_by_id[dataset_id].collector_ids
                for dataset_id in _SEC_MARKET_COMPANYFACTS_DATASET_IDS
            )
        ):
            raise RegistryError(
                "Registry 2.44 SEC market-universe predecessor identity drifted"
            )
        return registry

    collector = collector_by_id.get(_SEC_MARKET_COMPANYFACTS_COLLECTOR_ID)
    if (
        registry.source_sha256
        != _SEC_MARKET_COMPANYFACTS_REGISTRY_SOURCE_SHA256
        or hashlib.sha256(payload).hexdigest()
        != _SEC_MARKET_COMPANYFACTS_REGISTRY_SOURCE_SHA256
        or collector is None
        or collector["handler"] != "company.sec_market_fundamentals"
        or collector["configuration_env"]
        != ["SEC_USER_AGENT_NAME", "SEC_USER_AGENT_EMAIL"]
        or collector["input_datasets"] != ["market.stage10.instruments"]
        or collector["output_datasets"]
        != list(_SEC_MARKET_COMPANYFACTS_DATASET_IDS)
        or collector["workload_bounds"]
        != {
            "max_bytes": 67_108_864,
            "max_requests": 2,
            "max_rows": 50_000,
            "max_seconds": 120,
        }
        or any(
            dataset_by_id[dataset_id].collector_ids.count(
                _SEC_MARKET_COMPANYFACTS_COLLECTOR_ID
            )
            != 1
            for dataset_id in _SEC_MARKET_COMPANYFACTS_DATASET_IDS
        )
        or any(
            step.collector_id == _SEC_MARKET_COMPANYFACTS_COLLECTOR_ID
            for job in registry.jobs
            for step in job.steps
        )
    ):
        raise RegistryError("Current SEC market-universe registry identity drifted")

    projected_collectors = tuple(
        item
        for item in registry.collectors
        if str(item["id"]) != _SEC_MARKET_COMPANYFACTS_COLLECTOR_ID
    )
    projected_datasets = tuple(
        replace(
            item,
            collector_ids=tuple(
                collector_id
                for collector_id in item.collector_ids
                if collector_id != _SEC_MARKET_COMPANYFACTS_COLLECTOR_ID
            ),
        )
        for item in registry.datasets
    )
    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.44.0"
    raw["collectors"] = [
        item
        for item in raw["collectors"]
        if item["id"] != _SEC_MARKET_COMPANYFACTS_COLLECTOR_ID
    ]
    raw["datasets"] = [
        {
            **item,
            "collector_ids": [
                collector_id
                for collector_id in item["collector_ids"]
                if collector_id != _SEC_MARKET_COMPANYFACTS_COLLECTOR_ID
            ],
        }
        for item in raw["datasets"]
    ]
    projected_payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(projected_payload).hexdigest()
        != _SEC_AAPL_COMPANYFACTS_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError("Canonical SEC market-universe registry projection drifted")
    return replace(
        registry,
        registry_version="2.44.0",
        datasets=projected_datasets,
        collectors=projected_collectors,
        raw=raw,
        source_sha256=_SEC_AAPL_COMPANYFACTS_REGISTRY_SOURCE_SHA256,
    )


def sec_aapl_companyfacts_registry_profile(registry: Registry) -> Registry:
    """Project the additive AAPL SEC collector back to exact registry 2.43."""

    registry = sec_market_companyfacts_registry_profile(registry)
    version = (registry.schema_version, registry.registry_version)
    if version not in {("1.9.0", "2.44.0"), ("1.9.0", "2.43.0")}:
        return registry
    payload = (
        json.dumps(registry.raw, ensure_ascii=True, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    dataset_by_id = {item.id: item for item in registry.datasets}
    collector_by_id = {
        str(item["id"]): item for item in registry.collectors
    }
    if version == ("1.9.0", "2.43.0"):
        if (
            registry.source_sha256 != _ALPACA_SPY_OPTION_REGISTRY_SOURCE_SHA256
            or hashlib.sha256(payload).hexdigest()
            != _ALPACA_SPY_OPTION_REGISTRY_SOURCE_SHA256
            or _SEC_AAPL_COMPANYFACTS_COLLECTOR_ID in collector_by_id
            or any(
                _SEC_AAPL_COMPANYFACTS_COLLECTOR_ID
                in dataset_by_id[dataset_id].collector_ids
                for dataset_id in _SEC_AAPL_COMPANYFACTS_DATASET_IDS
            )
        ):
            raise RegistryError(
                "Registry 2.43 SEC AAPL predecessor identity drifted"
            )
        return registry

    collector = collector_by_id.get(_SEC_AAPL_COMPANYFACTS_COLLECTOR_ID)
    if (
        registry.source_sha256
        != _SEC_AAPL_COMPANYFACTS_REGISTRY_SOURCE_SHA256
        or hashlib.sha256(payload).hexdigest()
        != _SEC_AAPL_COMPANYFACTS_REGISTRY_SOURCE_SHA256
        or collector is None
        or collector["handler"] != "company.sec_aapl_fundamentals"
        or collector["configuration_env"]
        != ["SEC_USER_AGENT_NAME", "SEC_USER_AGENT_EMAIL"]
        or collector["output_datasets"]
        != list(_SEC_AAPL_COMPANYFACTS_DATASET_IDS)
        or collector["workload_bounds"]
        != {
            "max_bytes": 16_777_216,
            "max_requests": 2,
            "max_rows": 10_000,
            "max_seconds": 120,
        }
        or any(
            dataset_by_id[dataset_id].collector_ids.count(
                _SEC_AAPL_COMPANYFACTS_COLLECTOR_ID
            )
            != 1
            for dataset_id in _SEC_AAPL_COMPANYFACTS_DATASET_IDS
        )
        or any(
            step.collector_id == _SEC_AAPL_COMPANYFACTS_COLLECTOR_ID
            for job in registry.jobs
            for step in job.steps
        )
    ):
        raise RegistryError("Current SEC AAPL registry identity drifted")

    projected_collectors = tuple(
        item
        for item in registry.collectors
        if str(item["id"]) != _SEC_AAPL_COMPANYFACTS_COLLECTOR_ID
    )
    projected_datasets = tuple(
        replace(
            item,
            collector_ids=tuple(
                collector_id
                for collector_id in item.collector_ids
                if collector_id != _SEC_AAPL_COMPANYFACTS_COLLECTOR_ID
            ),
        )
        for item in registry.datasets
    )
    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.43.0"
    raw["collectors"] = [
        item
        for item in raw["collectors"]
        if item["id"] != _SEC_AAPL_COMPANYFACTS_COLLECTOR_ID
    ]
    raw["datasets"] = [
        {
            **item,
            "collector_ids": [
                collector_id
                for collector_id in item["collector_ids"]
                if collector_id != _SEC_AAPL_COMPANYFACTS_COLLECTOR_ID
            ],
        }
        for item in raw["datasets"]
    ]
    projected_payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(projected_payload).hexdigest()
        != _ALPACA_SPY_OPTION_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError("Canonical SEC AAPL registry projection drifted")
    return replace(
        registry,
        registry_version="2.43.0",
        datasets=projected_datasets,
        collectors=projected_collectors,
        raw=raw,
        source_sha256=_ALPACA_SPY_OPTION_REGISTRY_SOURCE_SHA256,
    )


def alpaca_spy_options_registry_profile(registry: Registry) -> Registry:
    """Project the additive Alpaca SPY collector back to exact registry 2.42."""

    registry = sec_aapl_companyfacts_registry_profile(registry)
    version = (registry.schema_version, registry.registry_version)
    if version not in {("1.9.0", "2.43.0"), ("1.9.0", "2.42.0")}:
        return registry
    payload = (
        json.dumps(registry.raw, ensure_ascii=True, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    dataset_by_id = {item.id: item for item in registry.datasets}
    collector_by_id = {
        str(item["id"]): item for item in registry.collectors
    }
    if version == ("1.9.0", "2.42.0"):
        if (
            registry.source_sha256
            != _MARKET_AVAILABLE_TICKER_V1_REGISTRY_SOURCE_SHA256
            or hashlib.sha256(payload).hexdigest()
            != _MARKET_AVAILABLE_TICKER_V1_REGISTRY_SOURCE_SHA256
            or _ALPACA_SPY_OPTION_COLLECTOR_ID in collector_by_id
            or any(
                _ALPACA_SPY_OPTION_COLLECTOR_ID
                in dataset_by_id[dataset_id].collector_ids
                for dataset_id in _ALPACA_SPY_OPTION_DATASET_IDS
            )
        ):
            raise RegistryError(
                "Registry 2.42 Alpaca predecessor identity drifted"
            )
        return registry

    if (
        registry.source_sha256 != _ALPACA_SPY_OPTION_REGISTRY_SOURCE_SHA256
        or hashlib.sha256(payload).hexdigest()
        != _ALPACA_SPY_OPTION_REGISTRY_SOURCE_SHA256
        or _ALPACA_SPY_OPTION_COLLECTOR_ID not in collector_by_id
        or any(
            dataset_by_id[dataset_id].collector_ids.count(
                _ALPACA_SPY_OPTION_COLLECTOR_ID
            )
            != 1
            for dataset_id in _ALPACA_SPY_OPTION_DATASET_IDS
        )
        or any(
            step.collector_id == _ALPACA_SPY_OPTION_COLLECTOR_ID
            for job in registry.jobs
            for step in job.steps
        )
    ):
        raise RegistryError("Current Alpaca SPY options registry identity drifted")

    projected_collectors = tuple(
        item
        for item in registry.collectors
        if str(item["id"]) != _ALPACA_SPY_OPTION_COLLECTOR_ID
    )
    projected_datasets = tuple(
        replace(
            item,
            collector_ids=tuple(
                collector_id
                for collector_id in item.collector_ids
                if collector_id != _ALPACA_SPY_OPTION_COLLECTOR_ID
            ),
        )
        for item in registry.datasets
    )
    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.42.0"
    raw["collectors"] = [
        item
        for item in raw["collectors"]
        if item["id"] != _ALPACA_SPY_OPTION_COLLECTOR_ID
    ]
    raw["datasets"] = [
        {
            **item,
            "collector_ids": [
                collector_id
                for collector_id in item["collector_ids"]
                if collector_id != _ALPACA_SPY_OPTION_COLLECTOR_ID
            ],
        }
        for item in raw["datasets"]
    ]
    projected_payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(projected_payload).hexdigest()
        != _MARKET_AVAILABLE_TICKER_V1_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError("Canonical Alpaca SPY registry projection drifted")
    return replace(
        registry,
        registry_version="2.42.0",
        datasets=projected_datasets,
        collectors=projected_collectors,
        raw=raw,
        source_sha256=_MARKET_AVAILABLE_TICKER_V1_REGISTRY_SOURCE_SHA256,
    )


def market_available_ticker_v1_registry_profile(
    registry: Registry,
) -> Registry:
    """Project the additive available-ticker tool back to exact registry 2.41."""

    registry = alpaca_spy_options_registry_profile(registry)
    version = (registry.schema_version, registry.registry_version)
    if version not in {("1.9.0", "2.42.0"), ("1.9.0", "2.41.0")}:
        return registry
    payload = (
        json.dumps(registry.raw, ensure_ascii=True, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    if version == ("1.9.0", "2.41.0"):
        if (
            registry.source_sha256
            != _MARKET_PRICE_SERIES_V1_REGISTRY_SOURCE_SHA256
            or hashlib.sha256(payload).hexdigest()
            != _MARKET_PRICE_SERIES_V1_REGISTRY_SOURCE_SHA256
            or tuple(str(item["id"]) for item in registry.tools)
            != _PRE_MARKET_AVAILABLE_TICKER_TOOL_NAMES
            or _tool_policy_variant_inventory(registry) != _POLICY_VARIANTS_2_39
            or registry.raw.get("tool_version_schema_catalog")
            != {
                "schema_id": VERSIONED_CATALOG_ID,
                "schema_version": "2.7.0",
                "resource": "quant_data/generated/tool_contract_schemas_v2.json",
                "sha256": _MARKET_PRICE_SERIES_V1_CATALOG_SOURCE_SHA256,
            }
        ):
            raise RegistryError(
                "Registry 2.41 available-ticker predecessor identity drifted"
            )
        return registry

    tool_names = tuple(str(item["id"]) for item in registry.tools)
    if (
        registry.source_sha256
        != _MARKET_AVAILABLE_TICKER_V1_REGISTRY_SOURCE_SHA256
        or hashlib.sha256(payload).hexdigest()
        != _MARKET_AVAILABLE_TICKER_V1_REGISTRY_SOURCE_SHA256
        or tool_names != _PRE_CANONICAL_ACCESS_TOOL_NAMES
        or _tool_policy_variant_inventory(registry) != _POLICY_VARIANTS_2_39
        or registry.raw.get("tool_version_schema_catalog")
        != {
            "schema_id": VERSIONED_CATALOG_ID,
            "schema_version": "2.8.0",
            "resource": "quant_data/generated/tool_contract_schemas_v2.json",
            "sha256": _MARKET_AVAILABLE_TICKER_V1_CATALOG_SOURCE_SHA256,
        }
        or registry.version_policy("market.get_available_ticker") is not None
    ):
        raise RegistryError("Current available-ticker registry identity drifted")
    declaration = registry.tool("market.get_available_ticker")
    raw_declarations = [
        item
        for item in registry.raw["tools"]
        if item["id"] == "market.get_available_ticker"
    ]
    if (
        len(raw_declarations) != 1
        or dict(declaration) != raw_declarations[0]
        or declaration["version"] != "1.0.0"
        or declaration["operation_graph_id"]
        != "tool_platform.market.get_available_ticker.v1"
        or declaration["datasets"]
        != [
            "market.stage10.instruments",
            "market.stage10.daily_prices",
            "market.stage10.source_evidence",
        ]
    ):
        raise RegistryError("Current available-ticker declaration drifted")
    expected_dataset_ids = frozenset(declaration["datasets"])
    if (
        frozenset(
            item.id
            for item in registry.datasets
            if "market.get_available_ticker" in item.tool_ids
        )
        != expected_dataset_ids
    ):
        raise RegistryError(
            "Current available-ticker dataset bindings drifted"
        )

    projected_tools = tuple(
        item
        for item in registry.tools
        if str(item["id"]) != "market.get_available_ticker"
    )
    projected_datasets = tuple(
        replace(
            item,
            tool_ids=tuple(
                tool_id
                for tool_id in item.tool_ids
                if tool_id != "market.get_available_ticker"
            ),
        )
        for item in registry.datasets
    )
    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.41.0"
    raw["tools"] = [
        item
        for item in raw["tools"]
        if item["id"] != "market.get_available_ticker"
    ]
    raw["datasets"] = [
        {
            **item,
            "tool_ids": [
                tool_id
                for tool_id in item["tool_ids"]
                if tool_id != "market.get_available_ticker"
            ],
        }
        for item in raw["datasets"]
    ]
    raw["presentation_order"]["tools"] = list(
        _PRE_MARKET_AVAILABLE_TICKER_TOOL_NAMES
    )
    raw["tool_version_schema_catalog"] = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.7.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _MARKET_PRICE_SERIES_V1_CATALOG_SOURCE_SHA256,
    }
    projected_payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(projected_payload).hexdigest()
        != _MARKET_PRICE_SERIES_V1_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError(
            "Canonical available-ticker registry projection drifted"
        )
    return replace(
        registry,
        registry_version="2.41.0",
        datasets=projected_datasets,
        tools=projected_tools,
        raw=raw,
        source_sha256=_MARKET_PRICE_SERIES_V1_REGISTRY_SOURCE_SHA256,
    )


def market_price_series_v1_registry_profile(registry: Registry) -> Registry:
    """Project the additive OHLC tool back to exact registry 2.40."""

    registry = market_available_ticker_v1_registry_profile(registry)
    version = (registry.schema_version, registry.registry_version)
    if version not in {("1.9.0", "2.41.0"), ("1.9.0", "2.40.0")}:
        return registry
    payload = (
        json.dumps(registry.raw, ensure_ascii=True, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    if version == ("1.9.0", "2.40.0"):
        if (
            registry.source_sha256
            != _FMP_CALENDAR_INCREMENTAL_REGISTRY_SOURCE_SHA256
            or hashlib.sha256(payload).hexdigest()
            != _FMP_CALENDAR_INCREMENTAL_REGISTRY_SOURCE_SHA256
            or tuple(str(item["id"]) for item in registry.tools)
            != PUBLIC_TOOL_NAMES
            or _tool_policy_variant_inventory(registry) != _POLICY_VARIANTS_2_39
            or registry.raw.get("tool_version_schema_catalog")
            != {
                "schema_id": VERSIONED_CATALOG_ID,
                "schema_version": "2.6.0",
                "resource": "quant_data/generated/tool_contract_schemas_v2.json",
                "sha256": _ECONOMETRICS_MODEL_SUITE_V3_CATALOG_SOURCE_SHA256,
            }
        ):
            raise RegistryError(
                "Registry 2.40 price-tool predecessor identity drifted"
            )
        return registry

    tool_names = tuple(str(item["id"]) for item in registry.tools)
    if (
        registry.source_sha256
        != _MARKET_PRICE_SERIES_V1_REGISTRY_SOURCE_SHA256
        or hashlib.sha256(payload).hexdigest()
        != _MARKET_PRICE_SERIES_V1_REGISTRY_SOURCE_SHA256
        or tool_names != _PRE_MARKET_AVAILABLE_TICKER_TOOL_NAMES
        or _tool_policy_variant_inventory(registry) != _POLICY_VARIANTS_2_39
        or registry.raw.get("tool_version_schema_catalog")
        != {
            "schema_id": VERSIONED_CATALOG_ID,
            "schema_version": "2.7.0",
            "resource": "quant_data/generated/tool_contract_schemas_v2.json",
            "sha256": _MARKET_PRICE_SERIES_V1_CATALOG_SOURCE_SHA256,
        }
        or registry.version_policy("market.get_price_series") is not None
    ):
        raise RegistryError("Current OHLC tool registry identity drifted")
    declaration = registry.tool("market.get_price_series")
    raw_declarations = [
        item
        for item in registry.raw["tools"]
        if item["id"] == "market.get_price_series"
    ]
    if (
        len(raw_declarations) != 1
        or dict(declaration) != raw_declarations[0]
        or declaration["version"] != "1.0.0"
        or declaration["operation_graph_id"]
        != "tool_platform.market.get_price_series.v1"
        or declaration["datasets"]
        != [
            "market.stage10.daily_prices",
            "market.stage10.source_evidence",
            "market.stage10.instruments",
        ]
    ):
        raise RegistryError("Current OHLC tool declaration drifted")
    expected_dataset_ids = frozenset(declaration["datasets"])
    if (
        frozenset(
            item.id
            for item in registry.datasets
            if "market.get_price_series" in item.tool_ids
        )
        != expected_dataset_ids
    ):
        raise RegistryError("Current OHLC tool dataset bindings drifted")

    projected_tools = tuple(
        item
        for item in registry.tools
        if str(item["id"]) != "market.get_price_series"
    )
    projected_datasets = tuple(
        replace(
            item,
            tool_ids=tuple(
                tool_id
                for tool_id in item.tool_ids
                if tool_id != "market.get_price_series"
            ),
        )
        for item in registry.datasets
    )
    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.40.0"
    raw["tools"] = [
        item
        for item in raw["tools"]
        if item["id"] != "market.get_price_series"
    ]
    raw["datasets"] = [
        {
            **item,
            "tool_ids": [
                tool_id
                for tool_id in item["tool_ids"]
                if tool_id != "market.get_price_series"
            ],
        }
        for item in raw["datasets"]
    ]
    raw["presentation_order"]["tools"] = list(PUBLIC_TOOL_NAMES)
    raw["tool_version_schema_catalog"] = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.6.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _ECONOMETRICS_MODEL_SUITE_V3_CATALOG_SOURCE_SHA256,
    }
    projected_payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(projected_payload).hexdigest()
        != _FMP_CALENDAR_INCREMENTAL_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError("Canonical OHLC tool registry projection drifted")
    return replace(
        registry,
        registry_version="2.40.0",
        datasets=projected_datasets,
        tools=projected_tools,
        raw=raw,
        source_sha256=_FMP_CALENDAR_INCREMENTAL_REGISTRY_SOURCE_SHA256,
    )


def fmp_calendar_incremental_registry_profile(registry: Registry) -> Registry:
    """Project compact FMP calendar retention back to exact registry 2.39."""

    registry = market_price_series_v1_registry_profile(registry)
    version = (registry.schema_version, registry.registry_version)
    if version not in {("1.9.0", "2.40.0"), ("1.9.0", "2.39.0")}:
        return registry

    dataset_by_id = {item.id: item for item in registry.datasets}
    collector_by_id = {
        str(item["id"]): item for item in registry.collectors
    }
    legacy = dataset_by_id.get(_FMP_WHOLESALE_CALENDAR_DATASET_ID)
    collector = collector_by_id.get(_FMP_WHOLESALE_CALENDAR_COLLECTOR_ID)

    if version == ("1.9.0", "2.39.0"):
        payload = (
            json.dumps(
                registry.raw, ensure_ascii=True, indent=2, sort_keys=True
            )
            + "\n"
        ).encode("utf-8")
        if (
            registry.source_sha256
            != _PRE_FMP_CALENDAR_INCREMENTAL_REGISTRY_SOURCE_SHA256
            or hashlib.sha256(payload).hexdigest()
            != _PRE_FMP_CALENDAR_INCREMENTAL_REGISTRY_SOURCE_SHA256
            or len(registry.migrations) != 39
            or len(registry.datasets) != 51
            or len(registry.collectors) != 50
            or any(
                item.id == _FMP_CALENDAR_INCREMENTAL_MIGRATION_ID
                for item in registry.migrations
            )
            or any(
                item.id in _FMP_CALENDAR_INCREMENTAL_DATASET_IDS
                for item in registry.datasets
            )
            or legacy is None
            or legacy.collector_ids
            != (_FMP_WHOLESALE_CALENDAR_COLLECTOR_ID,)
            or collector is None
            or collector.get("output_datasets")
            != [_FMP_WHOLESALE_CALENDAR_DATASET_ID]
            or collector.get("mutation_policy")
            != {
                "mode": "append_immutable_captures_and_rows",
                "unchanged": "zero_persistent_writes",
            }
        ):
            raise RegistryError(
                "Historical pre-incremental-calendar registry profile drifted"
            )
        return registry

    migration = next(
        (
            item
            for item in registry.migrations
            if item.id == _FMP_CALENDAR_INCREMENTAL_MIGRATION_ID
        ),
        None,
    )
    macro = next(
        (item for item in registry.stores if item.id == "macro"),
        None,
    )
    evidence = dataset_by_id.get(
        _FMP_CALENDAR_INCREMENTAL_EVIDENCE_DATASET_ID
    )
    events = dataset_by_id.get(_FMP_CALENDAR_INCREMENTAL_EVENT_DATASET_ID)
    if (
        registry.source_sha256
        != _FMP_CALENDAR_INCREMENTAL_REGISTRY_SOURCE_SHA256
        or len(registry.migrations) != 40
        or len(registry.datasets) != 53
        or len(registry.collectors) != 50
        or migration is None
        or migration.store != "macro"
        or migration.ordinal != 18
        or migration.resource
        != _FMP_CALENDAR_INCREMENTAL_MIGRATION_RESOURCE
        or migration.sha256 != _FMP_CALENDAR_INCREMENTAL_MIGRATION_SHA256
        or migration.dependencies != (_GDI_VINTAGE_MIGRATION_ID,)
        or migration.reconstruction_state != "fixture_validated"
        or macro is None
        or not macro.migration_order
        or macro.migration_order[-1]
        != _FMP_CALENDAR_INCREMENTAL_MIGRATION_ID
        or legacy is None
        or legacy.collector_ids
        or evidence is None
        or evidence.store != "macro"
        or evidence.layer != "evidence"
        or evidence.revision_policy != "current_state_capture"
        or evidence.relations
        != (
            "fmp_economic_calendar_fetch_receipts",
            "fmp_economic_calendar_latest_response_cache",
        )
        or evidence.collector_ids
        != (_FMP_WHOLESALE_CALENDAR_COLLECTOR_ID,)
        or events is None
        or events.store != "macro"
        or events.layer != "canonical"
        or events.revision_policy != "append_version"
        or events.relations
        != (
            "fmp_economic_calendar_raw_events",
            "fmp_economic_calendar_raw_event_versions",
        )
        or events.collector_ids
        != (_FMP_WHOLESALE_CALENDAR_COLLECTOR_ID,)
        or collector is None
        or collector.get("output_datasets")
        != list(_FMP_CALENDAR_INCREMENTAL_DATASET_IDS)
        or collector.get("mutation_policy")
        != {
            "mode": "append_event_versions_and_replace_latest_cache",
            "unchanged": "zero_persistent_writes",
        }
        or collector.get("semantic_identity")
        != {
            "includes": [
                "request_scope",
                "normalization_version",
                "normalized_complete_batch",
                "predecessor_receipt_id",
            ],
            "excludes": ["api_key", "captured_at", "http_headers", "source_row_order"],
        }
    ):
        raise RegistryError(
            "Canonical registry cannot reproduce revision 2.39"
        )

    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.39.0"
    raw["migrations"] = [
        item
        for item in raw["migrations"]
        if item["id"] != _FMP_CALENDAR_INCREMENTAL_MIGRATION_ID
    ]
    raw["datasets"] = [
        item
        for item in raw["datasets"]
        if item["id"] not in _FMP_CALENDAR_INCREMENTAL_DATASET_IDS
    ]

    raw_macro = next(
        (item for item in raw["stores"] if item.get("id") == "macro"),
        None,
    )
    if (
        raw_macro is None
        or not raw_macro.get("migration_order")
        or raw_macro["migration_order"][-1]
        != _FMP_CALENDAR_INCREMENTAL_MIGRATION_ID
    ):
        raise RegistryError("Canonical incremental calendar store order drifted")
    raw_macro["migration_order"] = raw_macro["migration_order"][:-1]

    raw_legacy = [
        item
        for item in raw["datasets"]
        if item.get("id") == _FMP_WHOLESALE_CALENDAR_DATASET_ID
    ]
    raw_collectors = [
        item
        for item in raw["collectors"]
        if item.get("id") == _FMP_WHOLESALE_CALENDAR_COLLECTOR_ID
    ]
    if len(raw_legacy) != 1 or len(raw_collectors) != 1:
        raise RegistryError("Canonical incremental calendar binding drifted")
    raw_legacy[0]["collector_ids"] = [
        _FMP_WHOLESALE_CALENDAR_COLLECTOR_ID
    ]
    raw_collectors[0]["output_datasets"] = [
        _FMP_WHOLESALE_CALENDAR_DATASET_ID
    ]
    raw_collectors[0]["mutation_policy"] = {
        "mode": "append_immutable_captures_and_rows",
        "unchanged": "zero_persistent_writes",
    }
    raw_collectors[0]["semantic_identity"]["includes"] = [
        "request_scope",
        "normalization_version",
        "normalized_complete_batch",
    ]

    projected_payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(projected_payload).hexdigest()
        != _PRE_FMP_CALENDAR_INCREMENTAL_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError(
            "Canonical incremental calendar registry projection drifted"
        )

    projected_collectors = tuple(
        (
            {
                **copy.deepcopy(dict(item)),
                "output_datasets": [_FMP_WHOLESALE_CALENDAR_DATASET_ID],
                "mutation_policy": {
                    "mode": "append_immutable_captures_and_rows",
                    "unchanged": "zero_persistent_writes",
                },
                "semantic_identity": {
                    **copy.deepcopy(dict(item["semantic_identity"])),
                    "includes": [
                        "request_scope",
                        "normalization_version",
                        "normalized_complete_batch",
                    ],
                },
            }
            if str(item["id"]) == _FMP_WHOLESALE_CALENDAR_COLLECTOR_ID
            else item
        )
        for item in registry.collectors
    )
    return replace(
        registry,
        registry_version="2.39.0",
        stores=tuple(
            replace(item, migration_order=item.migration_order[:-1])
            if item.id == "macro"
            else item
            for item in registry.stores
        ),
        migrations=tuple(
            item
            for item in registry.migrations
            if item.id != _FMP_CALENDAR_INCREMENTAL_MIGRATION_ID
        ),
        datasets=tuple(
            replace(
                item,
                collector_ids=(_FMP_WHOLESALE_CALENDAR_COLLECTOR_ID,),
            )
            if item.id == _FMP_WHOLESALE_CALENDAR_DATASET_ID
            else item
            for item in registry.datasets
            if item.id not in _FMP_CALENDAR_INCREMENTAL_DATASET_IDS
        ),
        collectors=projected_collectors,
        raw=raw,
        source_sha256=_PRE_FMP_CALENDAR_INCREMENTAL_REGISTRY_SOURCE_SHA256,
    )


def econometrics_model_suite_v3_registry_profile(
    registry: Registry,
) -> Registry:
    """Project the fixed-specification model suite back to exact 2.38."""

    registry = fmp_calendar_incremental_registry_profile(registry)
    version = (registry.schema_version, registry.registry_version)
    if version not in {("1.9.0", "2.39.0"), ("1.9.0", "2.38.0")}:
        return registry

    inventory = _tool_policy_variant_inventory(registry)
    if version == ("1.9.0", "2.38.0"):
        if (
            registry.source_sha256
            != _STRUCTURAL_BREAKS_V2_REGISTRY_SOURCE_SHA256
            or inventory != _POLICY_VARIANTS_2_38
            or registry.raw.get("tool_version_schema_catalog")
            != {
                "schema_id": VERSIONED_CATALOG_ID,
                "schema_version": "2.5.0",
                "resource": "quant_data/generated/tool_contract_schemas_v2.json",
                "sha256": _STRUCTURAL_BREAKS_V2_CATALOG_SOURCE_SHA256,
            }
        ):
            raise RegistryError(
                "Historical pre-model-suite-v3 registry profile drifted"
            )
        return registry

    if (
        registry.source_sha256
        != _ECONOMETRICS_MODEL_SUITE_V3_REGISTRY_SOURCE_SHA256
        or inventory != _POLICY_VARIANTS_2_39
        or registry.raw.get("tool_version_schema_catalog")
        != {
            "schema_id": VERSIONED_CATALOG_ID,
            "schema_version": "2.6.0",
            "resource": "quant_data/generated/tool_contract_schemas_v2.json",
            "sha256": _ECONOMETRICS_MODEL_SUITE_V3_CATALOG_SOURCE_SHA256,
        }
    ):
        raise RegistryError("Canonical registry cannot reproduce revision 2.38")

    retained_policies: list[Mapping[str, Any]] = []
    removed: list[tuple[str, str]] = []
    for policy in registry.tool_version_policies:
        projected_policy = copy.deepcopy(dict(policy))
        if projected_policy["tool"] == "econometrics.regression":
            projected_variants = []
            for variant in projected_policy["variants"]:
                if variant["version"] == "3.0.0":
                    removed.append(
                        (
                            str(projected_policy["tool"]),
                            str(variant["version"]),
                        )
                    )
                else:
                    projected_variants.append(variant)
            projected_policy["variants"] = projected_variants
        retained_policies.append(projected_policy)
    if tuple(removed) != (("econometrics.regression", "3.0.0"),):
        raise RegistryError("Econometrics model-suite v3 inventory drifted")

    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.38.0"
    raw["tool_versions"] = copy.deepcopy(retained_policies)
    raw["tool_version_schema_catalog"] = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.5.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _STRUCTURAL_BREAKS_V2_CATALOG_SOURCE_SHA256,
    }
    payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(payload).hexdigest()
        != _STRUCTURAL_BREAKS_V2_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError(
            "Canonical model-suite-v3 registry projection drifted"
        )
    return replace(
        registry,
        registry_version="2.38.0",
        tool_version_policies=tuple(retained_policies),
        raw=raw,
        source_sha256=_STRUCTURAL_BREAKS_V2_REGISTRY_SOURCE_SHA256,
    )


def structural_breaks_v2_registry_profile(registry: Registry) -> Registry:
    """Project the fixed-break Chow v2 contract back to exact 2.37."""
    registry = econometrics_model_suite_v3_registry_profile(registry)

    version = (registry.schema_version, registry.registry_version)
    if version not in {("1.9.0", "2.38.0"), ("1.9.0", "2.37.0")}:
        return registry

    inventory = _tool_policy_variant_inventory(registry)
    if version == ("1.9.0", "2.37.0"):
        if (
            registry.source_sha256
            != _STATIONARITY_V21_REGISTRY_SOURCE_SHA256
            or inventory != _POLICY_VARIANTS_2_37
            or registry.raw.get("tool_version_schema_catalog")
            != {
                "schema_id": VERSIONED_CATALOG_ID,
                "schema_version": "2.4.0",
                "resource": "quant_data/generated/tool_contract_schemas_v2.json",
                "sha256": _STATIONARITY_V21_CATALOG_SOURCE_SHA256,
            }
        ):
            raise RegistryError(
                "Historical pre-structural-breaks-v2 registry profile drifted"
            )
        return registry

    if (
        registry.source_sha256 != _STRUCTURAL_BREAKS_V2_REGISTRY_SOURCE_SHA256
        or inventory != _POLICY_VARIANTS_2_38
        or registry.raw.get("tool_version_schema_catalog")
        != {
            "schema_id": VERSIONED_CATALOG_ID,
            "schema_version": "2.5.0",
            "resource": "quant_data/generated/tool_contract_schemas_v2.json",
            "sha256": _STRUCTURAL_BREAKS_V2_CATALOG_SOURCE_SHA256,
        }
    ):
        raise RegistryError("Canonical registry cannot reproduce revision 2.37")

    retained_policies = tuple(
        copy.deepcopy(dict(policy))
        for policy in registry.tool_version_policies
        if policy["tool"] != "econometrics.structural_breaks"
    )
    removed = tuple(
        (
            str(policy["tool"]),
            tuple(str(item["version"]) for item in policy["variants"]),
        )
        for policy in registry.tool_version_policies
        if policy["tool"] == "econometrics.structural_breaks"
    )
    if removed != (("econometrics.structural_breaks", ("2.0.0",)),):
        raise RegistryError("Structural-breaks v2 inventory drifted")

    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.37.0"
    raw["tool_versions"] = copy.deepcopy(list(retained_policies))
    raw["tool_version_schema_catalog"] = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.4.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _STATIONARITY_V21_CATALOG_SOURCE_SHA256,
    }
    payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(payload).hexdigest()
        != _STATIONARITY_V21_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError(
            "Canonical structural-breaks-v2 registry projection drifted"
        )
    return replace(
        registry,
        registry_version="2.37.0",
        tool_version_policies=retained_policies,
        raw=raw,
        source_sha256=_STATIONARITY_V21_REGISTRY_SOURCE_SHA256,
    )


def stationarity_v21_registry_profile(registry: Registry) -> Registry:
    """Project the KPSS stationarity v2.1 contract back to exact 2.36."""

    registry = structural_breaks_v2_registry_profile(registry)
    version = (registry.schema_version, registry.registry_version)
    if version not in {("1.9.0", "2.37.0"), ("1.9.0", "2.36.0")}:
        return registry

    inventory = _tool_policy_variant_inventory(registry)
    if version == ("1.9.0", "2.36.0"):
        if (
            registry.source_sha256
            != _ROBUST_ECONOMETRICS_V21_REGISTRY_SOURCE_SHA256
            or inventory != _POLICY_VARIANTS_2_36
            or registry.raw.get("tool_version_schema_catalog")
            != {
                "schema_id": VERSIONED_CATALOG_ID,
                "schema_version": "2.3.0",
                "resource": "quant_data/generated/tool_contract_schemas_v2.json",
                "sha256": _ROBUST_ECONOMETRICS_V21_CATALOG_SOURCE_SHA256,
            }
        ):
            raise RegistryError(
                "Historical pre-stationarity-v2.1 registry profile drifted"
            )
        return registry

    if (
        registry.source_sha256 != _STATIONARITY_V21_REGISTRY_SOURCE_SHA256
        or inventory != _POLICY_VARIANTS_2_37
        or registry.raw.get("tool_version_schema_catalog")
        != {
            "schema_id": VERSIONED_CATALOG_ID,
            "schema_version": "2.4.0",
            "resource": "quant_data/generated/tool_contract_schemas_v2.json",
            "sha256": _STATIONARITY_V21_CATALOG_SOURCE_SHA256,
        }
    ):
        raise RegistryError("Canonical registry cannot reproduce revision 2.36")

    retained_policies: list[Mapping[str, Any]] = []
    removed: list[tuple[str, str]] = []
    for policy in registry.tool_version_policies:
        projected_policy = copy.deepcopy(dict(policy))
        if projected_policy["tool"] == "econometrics.stationarity":
            projected_variants = []
            for variant in projected_policy["variants"]:
                if variant["version"] == "2.1.0":
                    removed.append(
                        (str(projected_policy["tool"]), str(variant["version"]))
                    )
                else:
                    projected_variants.append(variant)
            projected_policy["variants"] = projected_variants
        retained_policies.append(projected_policy)
    if tuple(removed) != (("econometrics.stationarity", "2.1.0"),):
        raise RegistryError("Stationarity v2.1 inventory drifted")

    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.36.0"
    raw["tool_versions"] = copy.deepcopy(retained_policies)
    raw["tool_version_schema_catalog"] = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.3.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _ROBUST_ECONOMETRICS_V21_CATALOG_SOURCE_SHA256,
    }
    payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(payload).hexdigest()
        != _ROBUST_ECONOMETRICS_V21_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError(
            "Canonical stationarity-v2.1 registry projection drifted"
        )
    return replace(
        registry,
        registry_version="2.36.0",
        tool_version_policies=tuple(retained_policies),
        raw=raw,
        source_sha256=_ROBUST_ECONOMETRICS_V21_REGISTRY_SOURCE_SHA256,
    )


def robust_econometrics_v21_registry_profile(
    registry: Registry,
) -> Registry:
    """Project robust econometrics v2.1 contracts back to exact 2.35."""

    registry = stationarity_v21_registry_profile(registry)
    version = (registry.schema_version, registry.registry_version)
    if version not in {("1.9.0", "2.36.0"), ("1.9.0", "2.35.0")}:
        return registry

    inventory = _tool_policy_variant_inventory(registry)
    if version == ("1.9.0", "2.35.0"):
        if (
            registry.source_sha256
            != _PRE_ROBUST_ECONOMETRICS_V21_REGISTRY_SOURCE_SHA256
            or inventory != _POLICY_VARIANTS_2_35
            or registry.raw.get("tool_version_schema_catalog")
            != {
                "schema_id": VERSIONED_CATALOG_ID,
                "schema_version": "2.2.0",
                "resource": (
                    "quant_data/generated/tool_contract_schemas_v2.json"
                ),
                "sha256": (
                    _PRE_ROBUST_ECONOMETRICS_V21_CATALOG_SOURCE_SHA256
                ),
            }
        ):
            raise RegistryError(
                "Historical pre-robust-econometrics registry profile drifted"
            )
        return registry

    if (
        registry.source_sha256
        != _ROBUST_ECONOMETRICS_V21_REGISTRY_SOURCE_SHA256
        or inventory != _POLICY_VARIANTS_2_36
        or registry.raw.get("tool_version_schema_catalog")
        != {
            "schema_id": VERSIONED_CATALOG_ID,
            "schema_version": "2.3.0",
            "resource": "quant_data/generated/tool_contract_schemas_v2.json",
            "sha256": _ROBUST_ECONOMETRICS_V21_CATALOG_SOURCE_SHA256,
        }
    ):
        raise RegistryError("Canonical registry cannot reproduce revision 2.35")

    retained_policies: list[Mapping[str, Any]] = []
    removed: list[tuple[str, str]] = []
    for policy in registry.tool_version_policies:
        projected_policy = copy.deepcopy(dict(policy))
        projected_variants = []
        for variant in projected_policy["variants"]:
            if variant["version"] == "2.1.0":
                removed.append(
                    (str(projected_policy["tool"]), str(variant["version"]))
                )
            else:
                projected_variants.append(variant)
        projected_policy["variants"] = projected_variants
        retained_policies.append(projected_policy)
    if tuple(removed) != (
        ("econometrics.regression", "2.1.0"),
        ("econometrics.rolling_regression", "2.1.0"),
    ):
        raise RegistryError("Robust econometrics v2.1 inventory drifted")

    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.35.0"
    raw["tool_versions"] = copy.deepcopy(retained_policies)
    raw["tool_version_schema_catalog"] = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.2.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _PRE_ROBUST_ECONOMETRICS_V21_CATALOG_SOURCE_SHA256,
    }
    payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(payload).hexdigest()
        != _PRE_ROBUST_ECONOMETRICS_V21_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError(
            "Canonical robust-econometrics registry projection drifted"
        )
    return replace(
        registry,
        registry_version="2.35.0",
        tool_version_policies=tuple(retained_policies),
        raw=raw,
        source_sha256=_PRE_ROBUST_ECONOMETRICS_V21_REGISTRY_SOURCE_SHA256,
    )



def bea_personal_income_registry_profile(registry: Registry) -> Registry:
    """Project the BEA personal-income collector back to exact 2.34."""

    registry = robust_econometrics_v21_registry_profile(registry)
    version = (registry.schema_version, registry.registry_version)
    if version not in {("1.9.0", "2.35.0"), ("1.9.0", "2.34.0")}:
        return registry

    target_ids = set(_BEA_PERSONAL_INCOME_DATASET_IDS)
    target_datasets = tuple(
        item for item in registry.datasets if item.id in target_ids
    )
    if len(target_datasets) != len(target_ids):
        raise RegistryError("BEA personal-income dataset declarations drifted")

    if version == ("1.9.0", "2.34.0"):
        payload = (
            json.dumps(
                registry.raw, ensure_ascii=True, indent=2, sort_keys=True
            )
            + "\n"
        ).encode("utf-8")
        if (
            registry.source_sha256 != _ECONOMETRICS_V2_REGISTRY_SOURCE_SHA256
            or hashlib.sha256(payload).hexdigest()
            != _ECONOMETRICS_V2_REGISTRY_SOURCE_SHA256
            or len(registry.migrations) != 39
            or len(registry.datasets) != 51
            or len(registry.collectors) != 49
            or _BEA_PERSONAL_INCOME_COLLECTOR_ID
            in {str(item["id"]) for item in registry.collectors}
            or any(
                _BEA_PERSONAL_INCOME_COLLECTOR_ID in item.collector_ids
                for item in target_datasets
            )
        ):
            raise RegistryError(
                "Historical pre-BEA personal-income registry profile drifted"
            )
        return registry

    expected_collector = {
        "configuration_env": ["BEA_API_KEY"],
        "handler": "macro.bea_personal_income_history",
        "id": _BEA_PERSONAL_INCOME_COLLECTOR_ID,
        "input_datasets": [],
        "mutation_policy": {
            "mode": "append_versions_and_snapshot_membership",
            "unchanged": "zero_persistent_writes",
        },
        "network": True,
        "output_datasets": list(_BEA_PERSONAL_INCOME_DATASET_IDS),
        "physical_locks": "derived_from_output_store_paths",
        "retry_policy": {
            "backoff": "none_single_attempt",
            "honor_retry_after": False,
            "max_attempts": 1,
            "transient_classes": [],
        },
        "schedule_eligibility": {"mode": "manual_only"},
        "semantic_identity": {
            "excludes": [
                "api_key",
                "captured_at",
                "http_headers",
                "source_row_order",
            ],
            "includes": [
                "request_scope",
                "normalization_version",
                "normalized_observations",
            ],
        },
        "version": "1.0.0",
        "workload_bounds": {
            "max_bytes": 16_777_216,
            "max_requests": 1,
            "max_rows": 5_000,
            "max_seconds": 60,
        },
    }
    collector_by_id = {
        str(item["id"]): item for item in registry.collectors
    }
    historical_collectors: dict[str, tuple[str, ...]] = {}
    for dataset in target_datasets:
        if (
            not dataset.collector_ids
            or dataset.collector_ids[-1]
            != _BEA_PERSONAL_INCOME_COLLECTOR_ID
            or dataset.collector_ids.count(
                _BEA_PERSONAL_INCOME_COLLECTOR_ID
            )
            != 1
        ):
            raise RegistryError("BEA personal-income dataset binding drifted")
        historical_collectors[dataset.id] = dataset.collector_ids[:-1]

    if (
        registry.source_sha256 != _BEA_PERSONAL_INCOME_REGISTRY_SOURCE_SHA256
        or len(registry.migrations) != 39
        or len(registry.datasets) != 51
        or len(registry.collectors) != 50
        or dict(
            collector_by_id.get(_BEA_PERSONAL_INCOME_COLLECTOR_ID, {})
        )
        != expected_collector
        or any(
            step.collector_id == _BEA_PERSONAL_INCOME_COLLECTOR_ID
            for job in registry.jobs
            for step in job.steps
        )
    ):
        raise RegistryError("Canonical registry cannot reproduce revision 2.34")

    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.34.0"
    raw["collectors"] = [
        item
        for item in raw["collectors"]
        if item["id"] != _BEA_PERSONAL_INCOME_COLLECTOR_ID
    ]
    raw_target_ids: set[str] = set()
    for item in raw["datasets"]:
        dataset_id = item.get("id")
        if dataset_id not in historical_collectors:
            continue
        if tuple(item.get("collector_ids", ())) != (
            historical_collectors[dataset_id]
            + (_BEA_PERSONAL_INCOME_COLLECTOR_ID,)
        ):
            raise RegistryError("Canonical BEA personal-income binding drifted")
        item["collector_ids"] = list(historical_collectors[dataset_id])
        raw_target_ids.add(str(dataset_id))
    if raw_target_ids != target_ids:
        raise RegistryError("Canonical BEA personal-income inventory drifted")

    projected_payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(projected_payload).hexdigest()
        != _ECONOMETRICS_V2_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError(
            "Canonical BEA personal-income registry projection drifted"
        )
    return replace(
        registry,
        registry_version="2.34.0",
        datasets=tuple(
            replace(item, collector_ids=historical_collectors[item.id])
            if item.id in historical_collectors
            else item
            for item in registry.datasets
        ),
        collectors=tuple(
            item
            for item in registry.collectors
            if str(item["id"]) != _BEA_PERSONAL_INCOME_COLLECTOR_ID
        ),
        raw=raw,
        source_sha256=_ECONOMETRICS_V2_REGISTRY_SOURCE_SHA256,
    )


def econometrics_v2_registry_profile(registry: Registry) -> Registry:
    """Project additive econometrics v2 contracts back to exact 2.33."""

    registry = bea_personal_income_registry_profile(registry)
    predecessor_tools = _VERSIONED_TOOL_NAMES_2_35[:5]
    version = (registry.schema_version, registry.registry_version)
    if version not in {("1.9.0", "2.34.0"), ("1.9.0", "2.33.0")}:
        return registry
    if version == ("1.9.0", "2.33.0"):
        if (
            registry.source_sha256
            != _PRE_ECONOMETRICS_V2_REGISTRY_SOURCE_SHA256
            or tuple(
                policy["tool"] for policy in registry.tool_version_policies
            )
            != predecessor_tools
        ):
            raise RegistryError(
                "Historical pre-econometrics-v2 registry profile drifted"
            )
        return registry
    if (
        registry.source_sha256 != _ECONOMETRICS_V2_REGISTRY_SOURCE_SHA256
        or tuple(policy["tool"] for policy in registry.tool_version_policies)
        != _VERSIONED_TOOL_NAMES_2_35
    ):
        raise RegistryError("Canonical registry cannot reproduce revision 2.33")

    retained_policies = tuple(
        policy
        for policy in registry.tool_version_policies
        if policy["tool"] not in _VERSIONED_ECONOMETRICS_TOOLS_2_34
    )
    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.33.0"
    raw["tool_versions"] = [
        copy.deepcopy(dict(policy)) for policy in retained_policies
    ]
    raw["tool_version_schema_catalog"] = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.1.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _PRE_ECONOMETRICS_V2_CATALOG_SOURCE_SHA256,
    }
    payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(payload).hexdigest()
        != _PRE_ECONOMETRICS_V2_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError("Canonical econometrics-v2 registry projection drifted")
    return replace(
        registry,
        registry_version="2.33.0",
        tool_version_policies=retained_policies,
        raw=raw,
        source_sha256=_PRE_ECONOMETRICS_V2_REGISTRY_SOURCE_SHA256,
    )


def timeseries_analysis_v2_registry_profile(registry: Registry) -> Registry:
    """Project composable Stage 10 statistics back to exact registry 2.32."""

    registry = econometrics_v2_registry_profile(registry)
    version = (registry.schema_version, registry.registry_version)
    if version not in {("1.9.0", "2.33.0"), ("1.9.0", "2.32.0")}:
        return registry
    if version == ("1.9.0", "2.32.0"):
        if (
            registry.source_sha256
            != _PRE_TIMESERIES_ANALYSIS_V2_REGISTRY_SOURCE_SHA256
            or tuple(
                policy["tool"] for policy in registry.tool_version_policies
            )
            != VERSIONED_MARKET_RETURN_TOOLS
        ):
            raise RegistryError(
                "Historical pre-timeseries-v2 registry profile drifted"
            )
        return registry
    if (
        registry.source_sha256
        != _TIMESERIES_ANALYSIS_V2_REGISTRY_SOURCE_SHA256
        or tuple(
            policy["tool"] for policy in registry.tool_version_policies
        )
        != (
            *VERSIONED_MARKET_RETURN_TOOLS,
            *(
                item
                for item in VERSIONED_TIMESERIES_ANALYSIS_TOOLS
                if item != "timeseries.transform"
            ),
        )
    ):
        raise RegistryError("Canonical registry cannot reproduce revision 2.32")

    retained_policies = tuple(
        policy
        for policy in registry.tool_version_policies
        if policy["tool"] in VERSIONED_MARKET_RETURN_TOOLS
    )
    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.32.0"
    raw["tool_versions"] = [
        copy.deepcopy(dict(policy)) for policy in retained_policies
    ]
    raw["tool_version_schema_catalog"] = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": "2.0.0",
        "resource": "quant_data/generated/tool_contract_schemas_v2.json",
        "sha256": _PRE_TIMESERIES_ANALYSIS_V2_CATALOG_SOURCE_SHA256,
    }
    payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(payload).hexdigest()
        != _PRE_TIMESERIES_ANALYSIS_V2_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError("Canonical timeseries-v2 registry projection drifted")
    return replace(
        registry,
        registry_version="2.32.0",
        tool_version_policies=retained_policies,
        raw=raw,
        source_sha256=_PRE_TIMESERIES_ANALYSIS_V2_REGISTRY_SOURCE_SHA256,
    )


def market_return_v2_registry_profile(registry: Registry) -> Registry:
    """Project additive public return-tool v2 contracts back to exact 2.31."""

    registry = timeseries_analysis_v2_registry_profile(registry)
    version = (registry.schema_version, registry.registry_version)
    if version not in {("1.9.0", "2.32.0"), ("1.8.0", "2.31.0")}:
        return registry
    if version == ("1.8.0", "2.31.0"):
        if (
            registry.source_sha256
            != _PRE_MARKET_RETURN_V2_REGISTRY_SOURCE_SHA256
            or registry.tool_version_policies
            or "tool_versions" in registry.raw
            or "tool_version_schema_catalog" in registry.raw
        ):
            raise RegistryError("Historical pre-return-v2 registry profile drifted")
        return registry
    if (
        version != ("1.9.0", "2.32.0")
        or registry.source_sha256 != _MARKET_RETURN_V2_REGISTRY_SOURCE_SHA256
        or tuple(policy["tool"] for policy in registry.tool_version_policies)
        != VERSIONED_MARKET_RETURN_TOOLS
    ):
        raise RegistryError("Canonical registry cannot reproduce revision 2.31")

    affected = frozenset(VERSIONED_MARKET_RETURN_TOOLS)
    stage10_ids = {
        "market.stage10.daily_prices",
        "market.stage10.source_evidence",
        "market.stage10.instruments",
    }
    datasets = tuple(
        replace(
            item,
            tool_ids=tuple(
                tool_id for tool_id in item.tool_ids if tool_id not in affected
            ),
        )
        if item.id in stage10_ids
        else item
        for item in registry.datasets
    )
    raw = copy.deepcopy(dict(registry.raw))
    raw["schema_version"] = "1.8.0"
    raw["registry_version"] = "2.31.0"
    raw.pop("tool_versions", None)
    raw.pop("tool_version_schema_catalog", None)
    for dataset in raw["datasets"]:
        if dataset.get("id") in stage10_ids:
            dataset["tool_ids"] = [
                tool_id
                for tool_id in dataset["tool_ids"]
                if tool_id not in affected
            ]
    payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(payload).hexdigest()
        != _PRE_MARKET_RETURN_V2_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError("Canonical return-v2 registry projection drifted")
    return replace(
        registry,
        schema_version="1.8.0",
        registry_version="2.31.0",
        datasets=datasets,
        tool_version_policies=(),
        raw=raw,
        source_sha256=_PRE_MARKET_RETURN_V2_REGISTRY_SOURCE_SHA256,
    )




def gdi_vintage_registry_profile(registry: Registry) -> Registry:
    """Project the additive GDI migration back to exact registry 2.30."""

    registry = market_return_v2_registry_profile(registry)

    version = (registry.schema_version, registry.registry_version)
    if version == ("1.8.0", "2.30.0"):
        payload = (
            json.dumps(registry.raw, ensure_ascii=True, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8")
        if (
            registry.source_sha256
            != _PRE_GDI_VINTAGE_REGISTRY_SOURCE_SHA256
            or hashlib.sha256(payload).hexdigest()
            != _PRE_GDI_VINTAGE_REGISTRY_SOURCE_SHA256
            or len(registry.migrations) != 38
            or any(
                item.id == _GDI_VINTAGE_MIGRATION_ID
                for item in registry.migrations
            )
        ):
            raise RegistryError(
                "Historical pre-GDI registry profile drifted"
            )
        return registry

    migration = next(
        (
            item
            for item in registry.migrations
            if item.id == _GDI_VINTAGE_MIGRATION_ID
        ),
        None,
    )
    macro = next(
        (item for item in registry.stores if item.id == "macro"),
        None,
    )
    if (
        version != ("1.8.0", "2.31.0")
        or registry.source_sha256 != _GDI_VINTAGE_REGISTRY_SOURCE_SHA256
        or len(registry.migrations) != 39
        or len(registry.datasets) != 51
        or len(registry.collectors) != 49
        or migration is None
        or migration.store != "macro"
        or migration.ordinal != 17
        or migration.resource != _GDI_VINTAGE_MIGRATION_RESOURCE
        or migration.sha256 != _GDI_VINTAGE_MIGRATION_SHA256
        or migration.dependencies
        != ("macro:0016_fmp_calendar_wholesale_evidence",)
        or migration.reconstruction_state != "fixture_validated"
        or macro is None
        or not macro.migration_order
        or macro.migration_order[-1] != _GDI_VINTAGE_MIGRATION_ID
    ):
        raise RegistryError(
            "Canonical registry cannot reproduce revision 2.30"
        )

    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.30.0"
    raw["migrations"] = [
        item
        for item in raw["migrations"]
        if item["id"] != _GDI_VINTAGE_MIGRATION_ID
    ]
    raw_macro = next(
        (
            item
            for item in raw["stores"]
            if item.get("id") == "macro"
        ),
        None,
    )
    if (
        raw_macro is None
        or not raw_macro.get("migration_order")
        or raw_macro["migration_order"][-1] != _GDI_VINTAGE_MIGRATION_ID
    ):
        raise RegistryError("Canonical GDI raw store order drifted")
    raw_macro["migration_order"] = raw_macro["migration_order"][:-1]
    projected_payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(projected_payload).hexdigest()
        != _PRE_GDI_VINTAGE_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError(
            "Canonical GDI registry projection drifted"
        )
    return replace(
        registry,
        registry_version="2.30.0",
        stores=tuple(
            replace(item, migration_order=item.migration_order[:-1])
            if item.id == "macro"
            else item
            for item in registry.stores
        ),
        migrations=tuple(
            item
            for item in registry.migrations
            if item.id != _GDI_VINTAGE_MIGRATION_ID
        ),
        raw=raw,
        source_sha256=_PRE_GDI_VINTAGE_REGISTRY_SOURCE_SHA256,
    )


def bls_price_wage_productivity_registry_profile(
    registry: Registry,
) -> Registry:
    """Project the BLS price, wage, and productivity collector to exact 2.29."""

    registry = market_return_v2_registry_profile(registry)

    version = (registry.schema_version, registry.registry_version)
    if version == ("1.8.0", "2.31.0"):
        registry = gdi_vintage_registry_profile(registry)
        version = (registry.schema_version, registry.registry_version)
    dataset_by_id = {item.id: item for item in registry.datasets}
    target_datasets = tuple(
        dataset_by_id.get(dataset_id)
        for dataset_id in _BLS_PRICE_WAGE_PRODUCTIVITY_DATASET_IDS
    )
    if any(item is None for item in target_datasets):
        raise RegistryError(
            "BLS price, wage, and productivity dataset declarations drifted"
        )

    if version == ("1.8.0", "2.29.0"):
        payload = (
            json.dumps(registry.raw, ensure_ascii=True, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8")
        if (
            registry.source_sha256
            != _PRE_BLS_PRICE_WAGE_PRODUCTIVITY_REGISTRY_SOURCE_SHA256
            or hashlib.sha256(payload).hexdigest()
            != _PRE_BLS_PRICE_WAGE_PRODUCTIVITY_REGISTRY_SOURCE_SHA256
            or len(registry.migrations) != 38
            or len(registry.datasets) != 51
            or len(registry.collectors) != 48
            or _BLS_PRICE_WAGE_PRODUCTIVITY_COLLECTOR_ID
            in {str(item["id"]) for item in registry.collectors}
            or any(
                _BLS_PRICE_WAGE_PRODUCTIVITY_COLLECTOR_ID
                in item.collector_ids
                for item in target_datasets
                if item is not None
            )
        ):
            raise RegistryError(
                "Historical pre-BLS price, wage, and productivity registry "
                "profile drifted"
            )
        return registry

    expected_collector = {
        "configuration_env": [],
        "handler": "macro.bls_price_wage_productivity_history",
        "id": _BLS_PRICE_WAGE_PRODUCTIVITY_COLLECTOR_ID,
        "input_datasets": [],
        "mutation_policy": {
            "mode": "append_versions_and_snapshot_membership",
            "unchanged": "zero_persistent_writes",
        },
        "network": True,
        "output_datasets": list(_BLS_PRICE_WAGE_PRODUCTIVITY_DATASET_IDS),
        "physical_locks": "derived_from_output_store_paths",
        "retry_policy": {
            "backoff": "none_single_attempt",
            "honor_retry_after": False,
            "max_attempts": 1,
            "transient_classes": [],
        },
        "schedule_eligibility": {"mode": "manual_only"},
        "semantic_identity": {
            "excludes": [
                "captured_at",
                "http_headers",
                "source_row_order",
            ],
            "includes": [
                "request_scope",
                "normalization_version",
                "normalized_observations",
            ],
        },
        "version": "1.0.0",
        "workload_bounds": {
            "max_bytes": 16_777_216,
            "max_requests": 1,
            "max_rows": 5_000,
            "max_seconds": 60,
        },
    }
    collector_by_id = {
        str(item["id"]): item for item in registry.collectors
    }
    historical_collectors: dict[str, tuple[str, ...]] = {}
    for dataset_id, dataset in zip(
        _BLS_PRICE_WAGE_PRODUCTIVITY_DATASET_IDS,
        target_datasets,
        strict=True,
    ):
        assert dataset is not None
        if (
            not dataset.collector_ids
            or dataset.collector_ids[-1]
            != _BLS_PRICE_WAGE_PRODUCTIVITY_COLLECTOR_ID
            or dataset.collector_ids.count(
                _BLS_PRICE_WAGE_PRODUCTIVITY_COLLECTOR_ID
            )
            != 1
        ):
            raise RegistryError(
                "BLS price, wage, and productivity dataset binding drifted"
            )
        historical_collectors[dataset_id] = dataset.collector_ids[:-1]

    if (
        version != ("1.8.0", "2.30.0")
        or len(registry.migrations) != 38
        or len(registry.datasets) != 51
        or len(registry.collectors) != 49
        or dict(
            collector_by_id.get(
                _BLS_PRICE_WAGE_PRODUCTIVITY_COLLECTOR_ID, {}
            )
        )
        != expected_collector
        or any(
            step.collector_id == _BLS_PRICE_WAGE_PRODUCTIVITY_COLLECTOR_ID
            for job in registry.jobs
            for step in job.steps
        )
    ):
        raise RegistryError("Canonical registry cannot reproduce revision 2.29")

    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.29.0"
    raw["collectors"] = [
        item
        for item in raw["collectors"]
        if item["id"] != _BLS_PRICE_WAGE_PRODUCTIVITY_COLLECTOR_ID
    ]
    raw_target_ids: set[str] = set()
    for item in raw["datasets"]:
        dataset_id = item.get("id")
        if dataset_id not in historical_collectors:
            continue
        if tuple(item.get("collector_ids", ())) != (
            historical_collectors[dataset_id]
            + (_BLS_PRICE_WAGE_PRODUCTIVITY_COLLECTOR_ID,)
        ):
            raise RegistryError(
                "Canonical BLS price, wage, and productivity raw binding drifted"
            )
        item["collector_ids"] = list(historical_collectors[dataset_id])
        raw_target_ids.add(str(dataset_id))
    if raw_target_ids != set(_BLS_PRICE_WAGE_PRODUCTIVITY_DATASET_IDS):
        raise RegistryError(
            "Canonical BLS price, wage, and productivity dataset inventory "
            "drifted"
        )
    projected_payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(projected_payload).hexdigest()
        != _PRE_BLS_PRICE_WAGE_PRODUCTIVITY_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError(
            "Canonical BLS price, wage, and productivity registry projection "
            "drifted"
        )
    return replace(
        registry,
        registry_version="2.29.0",
        datasets=tuple(
            replace(item, collector_ids=historical_collectors[item.id])
            if item.id in historical_collectors
            else item
            for item in registry.datasets
        ),
        collectors=tuple(
            item
            for item in registry.collectors
            if str(item["id"])
            != _BLS_PRICE_WAGE_PRODUCTIVITY_COLLECTOR_ID
        ),
        raw=raw,
        source_sha256=_PRE_BLS_PRICE_WAGE_PRODUCTIVITY_REGISTRY_SOURCE_SHA256,
    )


def official_macro_extension_registry_profile(registry: Registry) -> Registry:
    """Project the Treasury, EIA gas, and NBER collectors back to exact 2.28."""

    registry = market_return_v2_registry_profile(registry)

    version = (registry.schema_version, registry.registry_version)
    if version in {
        ("1.8.0", "2.31.0"),
        ("1.8.0", "2.30.0"),
    }:
        registry = bls_price_wage_productivity_registry_profile(registry)
        version = (registry.schema_version, registry.registry_version)
    dataset_by_id = {item.id: item for item in registry.datasets}
    target_datasets = tuple(
        dataset_by_id.get(dataset_id)
        for dataset_id in _OFFICIAL_MACRO_EXTENSION_DATASET_IDS
    )
    if any(item is None for item in target_datasets):
        raise RegistryError("Official macro extension dataset declarations drifted")

    if version == ("1.8.0", "2.28.0"):
        payload = (
            json.dumps(registry.raw, ensure_ascii=True, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8")
        collector_ids = {str(item["id"]) for item in registry.collectors}
        if (
            registry.source_sha256
            != _PRE_OFFICIAL_MACRO_EXTENSION_REGISTRY_SOURCE_SHA256
            or hashlib.sha256(payload).hexdigest()
            != _PRE_OFFICIAL_MACRO_EXTENSION_REGISTRY_SOURCE_SHA256
            or len(registry.migrations) != 38
            or len(registry.datasets) != 51
            or len(registry.collectors) != 45
            or collector_ids.intersection(
                _OFFICIAL_MACRO_EXTENSION_COLLECTOR_IDS
            )
            or any(
                set(item.collector_ids).intersection(
                    _OFFICIAL_MACRO_EXTENSION_COLLECTOR_IDS
                )
                for item in target_datasets
                if item is not None
            )
        ):
            raise RegistryError(
                "Historical pre-official-macro-extension registry profile drifted"
            )
        return registry

    common = {
        "input_datasets": [],
        "mutation_policy": {
            "mode": "append_versions_and_snapshot_membership",
            "unchanged": "zero_persistent_writes",
        },
        "network": True,
        "output_datasets": list(_OFFICIAL_MACRO_EXTENSION_DATASET_IDS),
        "physical_locks": "derived_from_output_store_paths",
        "retry_policy": {
            "backoff": "none_single_attempt",
            "honor_retry_after": False,
            "max_attempts": 1,
            "transient_classes": [],
        },
        "schedule_eligibility": {"mode": "manual_only"},
        "version": "1.0.0",
    }
    variants = {
        "treasury_fiscal_data.macro.tga_closing_balance_history": (
            "macro.treasury_fiscal_tga_history",
            10_000,
            (),
            ("captured_at", "http_headers", "source_row_order"),
        ),
        "eia.macro.natural_gas_storage_history": (
            "macro.eia_natural_gas_storage_history",
            5_000,
            ("EIA_API_KEY",),
            ("api_key", "captured_at", "http_headers", "source_row_order"),
        ),
        "nber.macro.us_recession_history": (
            "macro.nber_us_recession_history",
            5_000,
            (),
            ("captured_at", "http_headers", "source_row_order"),
        ),
    }
    expected_collectors = {
        collector_id: {
            **common,
            "configuration_env": list(values[2]),
            "handler": values[0],
            "id": collector_id,
            "semantic_identity": {
                "excludes": list(values[3]),
                "includes": [
                    "request_scope",
                    "normalization_version",
                    "normalized_observations",
                ],
            },
            "workload_bounds": {
                "max_bytes": 16_777_216,
                "max_requests": 1,
                "max_rows": values[1],
                "max_seconds": 60,
            },
        }
        for collector_id, values in variants.items()
    }
    collector_by_id = {
        str(item["id"]): item for item in registry.collectors
    }
    suffix = _OFFICIAL_MACRO_EXTENSION_COLLECTOR_IDS
    historical_collectors: dict[str, tuple[str, ...]] = {}
    for dataset_id, dataset in zip(
        _OFFICIAL_MACRO_EXTENSION_DATASET_IDS,
        target_datasets,
        strict=True,
    ):
        assert dataset is not None
        if (
            dataset.collector_ids[-len(suffix) :] != suffix
            or any(
                dataset.collector_ids.count(collector_id) != 1
                for collector_id in suffix
            )
        ):
            raise RegistryError("Official macro extension dataset binding drifted")
        historical_collectors[dataset_id] = dataset.collector_ids[
            : -len(suffix)
        ]

    if (
        version != ("1.8.0", "2.29.0")
        or len(registry.migrations) != 38
        or len(registry.datasets) != 51
        or len(registry.collectors) != 48
        or any(
            collector_id not in collector_by_id
            or dict(collector_by_id[collector_id])
            != expected_collectors[collector_id]
            for collector_id in suffix
        )
        or any(
            step.collector_id in suffix
            for job in registry.jobs
            for step in job.steps
        )
    ):
        raise RegistryError("Canonical registry cannot reproduce revision 2.28")

    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.28.0"
    raw["collectors"] = [
        item for item in raw["collectors"] if item["id"] not in suffix
    ]
    raw_target_ids: set[str] = set()
    for item in raw["datasets"]:
        dataset_id = item.get("id")
        if dataset_id not in historical_collectors:
            continue
        if (
            tuple(item.get("collector_ids", ()))
            != historical_collectors[dataset_id] + suffix
        ):
            raise RegistryError(
                "Canonical official macro extension raw binding drifted"
            )
        item["collector_ids"] = list(historical_collectors[dataset_id])
        raw_target_ids.add(str(dataset_id))
    if raw_target_ids != set(_OFFICIAL_MACRO_EXTENSION_DATASET_IDS):
        raise RegistryError(
            "Canonical official macro extension dataset inventory drifted"
        )
    projected_payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(projected_payload).hexdigest()
        != _PRE_OFFICIAL_MACRO_EXTENSION_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError(
            "Canonical official macro extension registry projection drifted"
        )
    return replace(
        registry,
        registry_version="2.28.0",
        datasets=tuple(
            replace(item, collector_ids=historical_collectors[item.id])
            if item.id in historical_collectors
            else item
            for item in registry.datasets
        ),
        collectors=tuple(
            item
            for item in registry.collectors
            if str(item["id"]) not in suffix
        ),
        raw=raw,
        source_sha256=_PRE_OFFICIAL_MACRO_EXTENSION_REGISTRY_SOURCE_SHA256,
    )


def nyfed_cmdi_registry_profile(registry: Registry) -> Registry:
    """Project the NY Fed CMDI collector back to exact registry 2.27."""

    registry = market_return_v2_registry_profile(registry)

    version = (registry.schema_version, registry.registry_version)
    if version in {
        ("1.8.0", "2.31.0"),
        ("1.8.0", "2.30.0"),
        ("1.8.0", "2.29.0"),
    }:
        registry = official_macro_extension_registry_profile(registry)
        version = (registry.schema_version, registry.registry_version)
    dataset_by_id = {item.id: item for item in registry.datasets}
    target_datasets = tuple(
        dataset_by_id.get(dataset_id)
        for dataset_id in _NYFED_CMDI_DATASET_IDS
    )
    if any(item is None for item in target_datasets):
        raise RegistryError("NY Fed CMDI dataset declarations drifted")

    if version == ("1.8.0", "2.27.0"):
        payload = (
            json.dumps(
                registry.raw,
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        if (
            registry.source_sha256
            != _PRE_NYFED_CMDI_REGISTRY_SOURCE_SHA256
            or hashlib.sha256(payload).hexdigest()
            != _PRE_NYFED_CMDI_REGISTRY_SOURCE_SHA256
            or len(registry.migrations) != 38
            or len(registry.datasets) != 51
            or len(registry.collectors) != 44
            or _NYFED_CMDI_COLLECTOR_ID
            in {str(item["id"]) for item in registry.collectors}
            or any(
                _NYFED_CMDI_COLLECTOR_ID in item.collector_ids
                for item in target_datasets
                if item is not None
            )
        ):
            raise RegistryError(
                "Historical pre-NY-Fed-CMDI registry profile drifted"
            )
        return registry

    expected_collector = {
        "configuration_env": [],
        "handler": "macro.nyfed_cmdi_history",
        "id": _NYFED_CMDI_COLLECTOR_ID,
        "input_datasets": [],
        "mutation_policy": {
            "mode": "append_versions_and_snapshot_membership",
            "unchanged": "zero_persistent_writes",
        },
        "network": True,
        "output_datasets": list(_NYFED_CMDI_DATASET_IDS),
        "physical_locks": "derived_from_output_store_paths",
        "retry_policy": {
            "backoff": "none_single_attempt",
            "honor_retry_after": False,
            "max_attempts": 1,
            "transient_classes": [],
        },
        "schedule_eligibility": {"mode": "manual_only"},
        "semantic_identity": {
            "excludes": [
                "captured_at",
                "http_headers",
                "source_row_order",
            ],
            "includes": [
                "request_scope",
                "normalization_version",
                "normalized_observations",
            ],
        },
        "version": "1.0.0",
        "workload_bounds": {
            "max_bytes": 16_777_216,
            "max_requests": 1,
            "max_rows": 5_000,
            "max_seconds": 60,
        },
    }
    collector_by_id = {
        str(item["id"]): item for item in registry.collectors
    }
    historical_collectors: dict[str, tuple[str, ...]] = {}
    for dataset_id, dataset in zip(
        _NYFED_CMDI_DATASET_IDS,
        target_datasets,
        strict=True,
    ):
        assert dataset is not None
        if (
            not dataset.collector_ids
            or dataset.collector_ids[-1] != _NYFED_CMDI_COLLECTOR_ID
            or dataset.collector_ids.count(_NYFED_CMDI_COLLECTOR_ID)
            != 1
        ):
            raise RegistryError("NY Fed CMDI dataset binding drifted")
        historical_collectors[dataset_id] = dataset.collector_ids[:-1]

    if (
        version != ("1.8.0", "2.28.0")
        or len(registry.migrations) != 38
        or len(registry.datasets) != 51
        or len(registry.collectors) != 45
        or dict(
            collector_by_id.get(_NYFED_CMDI_COLLECTOR_ID, {})
        )
        != expected_collector
        or any(
            step.collector_id == _NYFED_CMDI_COLLECTOR_ID
            for job in registry.jobs
            for step in job.steps
        )
    ):
        raise RegistryError("Canonical registry cannot reproduce revision 2.27")

    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.27.0"
    raw["collectors"] = [
        item
        for item in raw["collectors"]
        if item["id"] != _NYFED_CMDI_COLLECTOR_ID
    ]
    raw_target_ids: set[str] = set()
    for item in raw["datasets"]:
        dataset_id = item.get("id")
        if dataset_id not in historical_collectors:
            continue
        if (
            tuple(item.get("collector_ids", ()))
            != historical_collectors[dataset_id]
            + (_NYFED_CMDI_COLLECTOR_ID,)
        ):
            raise RegistryError("Canonical NY Fed CMDI raw binding drifted")
        item["collector_ids"] = list(historical_collectors[dataset_id])
        raw_target_ids.add(str(dataset_id))
    if raw_target_ids != set(_NYFED_CMDI_DATASET_IDS):
        raise RegistryError(
            "Canonical NY Fed CMDI dataset inventory drifted"
        )
    projected_payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(projected_payload).hexdigest()
        != _PRE_NYFED_CMDI_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError("Canonical NY Fed CMDI projection drifted")
    return replace(
        registry,
        registry_version="2.27.0",
        datasets=tuple(
            replace(item, collector_ids=historical_collectors[item.id])
            if item.id in historical_collectors
            else item
            for item in registry.datasets
        ),
        collectors=tuple(
            item
            for item in registry.collectors
            if str(item["id"]) != _NYFED_CMDI_COLLECTOR_ID
        ),
        raw=raw,
        source_sha256=_PRE_NYFED_CMDI_REGISTRY_SOURCE_SHA256,
    )


def official_conditions_registry_profile(registry: Registry) -> Registry:
    """Project the three official-condition collectors back to exact 2.26."""

    registry = market_return_v2_registry_profile(registry)

    version = (registry.schema_version, registry.registry_version)
    if version in {
        ("1.8.0", "2.31.0"),
        ("1.8.0", "2.30.0"),
        ("1.8.0", "2.29.0"),
        ("1.8.0", "2.28.0"),
    }:
        registry = nyfed_cmdi_registry_profile(registry)
        version = (registry.schema_version, registry.registry_version)
    dataset_by_id = {item.id: item for item in registry.datasets}
    target_datasets = tuple(
        dataset_by_id.get(dataset_id)
        for dataset_id in _OFFICIAL_CONDITIONS_DATASET_IDS
    )
    if any(item is None for item in target_datasets):
        raise RegistryError("Official conditions dataset declarations drifted")

    if version == ("1.8.0", "2.26.0"):
        payload = (
            json.dumps(registry.raw, ensure_ascii=True, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8")
        collector_ids = {str(item["id"]) for item in registry.collectors}
        if (
            registry.source_sha256
            != _PRE_OFFICIAL_CONDITIONS_REGISTRY_SOURCE_SHA256
            or hashlib.sha256(payload).hexdigest()
            != _PRE_OFFICIAL_CONDITIONS_REGISTRY_SOURCE_SHA256
            or len(registry.migrations) != 38
            or len(registry.datasets) != 51
            or len(registry.collectors) != 41
            or collector_ids.intersection(_OFFICIAL_CONDITIONS_COLLECTOR_IDS)
            or any(
                set(item.collector_ids).intersection(
                    _OFFICIAL_CONDITIONS_COLLECTOR_IDS
                )
                for item in target_datasets
                if item is not None
            )
        ):
            raise RegistryError(
                "Historical pre-official-conditions registry profile drifted"
            )
        return registry

    common = {
        "configuration_env": [],
        "input_datasets": [],
        "mutation_policy": {
            "mode": "append_versions_and_snapshot_membership",
            "unchanged": "zero_persistent_writes",
        },
        "network": True,
        "output_datasets": list(_OFFICIAL_CONDITIONS_DATASET_IDS),
        "physical_locks": "derived_from_output_store_paths",
        "retry_policy": {
            "backoff": "none_single_attempt",
            "honor_retry_after": False,
            "max_attempts": 1,
            "transient_classes": [],
        },
        "schedule_eligibility": {"mode": "manual_only"},
        "semantic_identity": {
            "excludes": [
                "captured_at",
                "http_headers",
                "source_row_order",
            ],
            "includes": [
                "request_scope",
                "normalization_version",
                "normalized_observations",
            ],
        },
        "version": "1.0.0",
    }
    variants = {
        "federal_reserve.macro.h41_history": (
            "macro.federal_reserve_h41_history",
            1,
            20_000,
            120,
        ),
        "chicagofed.macro.nfci_history": (
            "macro.chicagofed_nfci_history",
            1,
            5_000,
            60,
        ),
        "bis.macro.credit_conditions_history": (
            "macro.bis_credit_conditions_history",
            2,
            5_000,
            120,
        ),
    }
    expected_collectors = {
        collector_id: {
            **common,
            "handler": values[0],
            "id": collector_id,
            "workload_bounds": {
                "max_bytes": 16_777_216,
                "max_requests": values[1],
                "max_rows": values[2],
                "max_seconds": values[3],
            },
        }
        for collector_id, values in variants.items()
    }
    collector_by_id = {
        str(item["id"]): item for item in registry.collectors
    }
    suffix = _OFFICIAL_CONDITIONS_COLLECTOR_IDS
    historical_collectors: dict[str, tuple[str, ...]] = {}
    for dataset_id, dataset in zip(
        _OFFICIAL_CONDITIONS_DATASET_IDS,
        target_datasets,
        strict=True,
    ):
        assert dataset is not None
        if (
            dataset.collector_ids[-len(suffix) :] != suffix
            or any(
                dataset.collector_ids.count(collector_id) != 1
                for collector_id in suffix
            )
        ):
            raise RegistryError("Official conditions dataset binding drifted")
        historical_collectors[dataset_id] = dataset.collector_ids[
            : -len(suffix)
        ]

    if (
        version != ("1.8.0", "2.27.0")
        or len(registry.migrations) != 38
        or len(registry.datasets) != 51
        or len(registry.collectors) != 44
        or any(
            collector_id not in collector_by_id
            or dict(collector_by_id[collector_id])
            != expected_collectors[collector_id]
            for collector_id in suffix
        )
        or any(
            step.collector_id in suffix
            for job in registry.jobs
            for step in job.steps
        )
    ):
        raise RegistryError("Canonical registry cannot reproduce revision 2.26")

    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.26.0"
    raw["collectors"] = [
        item for item in raw["collectors"] if item["id"] not in suffix
    ]
    raw_target_ids: set[str] = set()
    for item in raw["datasets"]:
        dataset_id = item.get("id")
        if dataset_id not in historical_collectors:
            continue
        if (
            tuple(item.get("collector_ids", ()))
            != historical_collectors[dataset_id] + suffix
        ):
            raise RegistryError(
                "Canonical official conditions raw binding drifted"
            )
        item["collector_ids"] = list(historical_collectors[dataset_id])
        raw_target_ids.add(str(dataset_id))
    if raw_target_ids != set(_OFFICIAL_CONDITIONS_DATASET_IDS):
        raise RegistryError(
            "Canonical official conditions dataset inventory drifted"
        )

    projected_payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(projected_payload).hexdigest()
        != _PRE_OFFICIAL_CONDITIONS_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError(
            "Canonical official conditions registry projection drifted"
        )
    datasets = tuple(
        replace(item, collector_ids=historical_collectors[item.id])
        if item.id in historical_collectors
        else item
        for item in registry.datasets
    )
    collectors = tuple(
        item
        for item in registry.collectors
        if str(item["id"]) not in suffix
    )
    return replace(
        registry,
        registry_version="2.26.0",
        datasets=datasets,
        collectors=collectors,
        raw=raw,
        source_sha256=_PRE_OFFICIAL_CONDITIONS_REGISTRY_SOURCE_SHA256,
    )


def nyfed_soma_summary_registry_profile(registry: Registry) -> Registry:
    """Project the NY Fed SOMA collector back to exact registry 2.25."""

    registry = market_return_v2_registry_profile(registry)

    version = (registry.schema_version, registry.registry_version)
    if version in {
        ("1.8.0", "2.31.0"),
        ("1.8.0", "2.30.0"),
        ("1.8.0", "2.29.0"),
        ("1.8.0", "2.28.0"),
        ("1.8.0", "2.27.0"),
    }:
        registry = official_conditions_registry_profile(registry)
        version = (registry.schema_version, registry.registry_version)
    dataset_by_id = {item.id: item for item in registry.datasets}
    target_datasets = tuple(
        dataset_by_id.get(dataset_id) for dataset_id in _NYFED_SOMA_DATASET_IDS
    )
    if any(item is None for item in target_datasets):
        raise RegistryError("NY Fed SOMA dataset declarations drifted")

    if version == ("1.8.0", "2.25.0"):
        payload = (
            json.dumps(registry.raw, ensure_ascii=True, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8")
        if (
            registry.source_sha256 != _PRE_NYFED_SOMA_REGISTRY_SOURCE_SHA256
            or hashlib.sha256(payload).hexdigest()
            != _PRE_NYFED_SOMA_REGISTRY_SOURCE_SHA256
            or len(registry.migrations) != 38
            or len(registry.datasets) != 51
            or len(registry.collectors) != 40
            or _NYFED_SOMA_COLLECTOR_ID
            in {str(item["id"]) for item in registry.collectors}
            or any(
                _NYFED_SOMA_COLLECTOR_ID in item.collector_ids
                for item in target_datasets
                if item is not None
            )
        ):
            raise RegistryError("Historical pre-NY-Fed-SOMA registry profile drifted")
        return registry

    expected_collector = {
        "configuration_env": [],
        "handler": "macro.nyfed_soma_summary_history",
        "id": _NYFED_SOMA_COLLECTOR_ID,
        "input_datasets": [],
        "mutation_policy": {
            "mode": "append_summary_versions_and_snapshot_membership",
            "unchanged": "zero_persistent_writes",
        },
        "network": True,
        "output_datasets": list(_NYFED_SOMA_DATASET_IDS),
        "physical_locks": "derived_from_output_store_paths",
        "retry_policy": {
            "backoff": "none_single_attempt",
            "honor_retry_after": False,
            "max_attempts": 1,
            "transient_classes": [],
        },
        "schedule_eligibility": {"mode": "manual_only"},
        "semantic_identity": {
            "excludes": [
                "captured_at",
                "http_headers",
                "source_row_order",
            ],
            "includes": [
                "request_scope",
                "normalization_version",
                "normalized_summary_components",
            ],
        },
        "version": "1.0.0",
        "workload_bounds": {
            "max_bytes": 16_777_216,
            "max_requests": 1,
            "max_rows": 20_000,
            "max_seconds": 60,
        },
    }
    collector_matches = tuple(
        item
        for item in registry.collectors
        if str(item["id"]) == _NYFED_SOMA_COLLECTOR_ID
    )
    historical_collectors: dict[str, tuple[str, ...]] = {}
    for dataset_id, dataset in zip(
        _NYFED_SOMA_DATASET_IDS,
        target_datasets,
        strict=True,
    ):
        assert dataset is not None
        if (
            not dataset.collector_ids
            or dataset.collector_ids[-1] != _NYFED_SOMA_COLLECTOR_ID
            or dataset.collector_ids.count(_NYFED_SOMA_COLLECTOR_ID) != 1
        ):
            raise RegistryError("NY Fed SOMA dataset binding drifted")
        historical_collectors[dataset_id] = dataset.collector_ids[:-1]

    if (
        version != ("1.8.0", "2.26.0")
        or len(registry.migrations) != 38
        or len(registry.datasets) != 51
        or len(registry.collectors) != 41
        or len(collector_matches) != 1
        or dict(collector_matches[0]) != expected_collector
        or any(
            step.collector_id == _NYFED_SOMA_COLLECTOR_ID
            for job in registry.jobs
            for step in job.steps
        )
    ):
        raise RegistryError("Canonical registry cannot reproduce revision 2.25")

    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.25.0"
    raw["collectors"] = [
        item for item in raw["collectors"] if item["id"] != _NYFED_SOMA_COLLECTOR_ID
    ]
    raw_target_ids: set[str] = set()
    for item in raw["datasets"]:
        dataset_id = item.get("id")
        if dataset_id not in historical_collectors:
            continue
        if (
            tuple(item.get("collector_ids", ()))
            != historical_collectors[dataset_id] + (_NYFED_SOMA_COLLECTOR_ID,)
        ):
            raise RegistryError("Canonical NY Fed SOMA raw binding drifted")
        item["collector_ids"] = list(historical_collectors[dataset_id])
        raw_target_ids.add(str(dataset_id))
    if raw_target_ids != set(_NYFED_SOMA_DATASET_IDS):
        raise RegistryError("Canonical NY Fed SOMA dataset inventory drifted")

    projected_payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(projected_payload).hexdigest()
        != _PRE_NYFED_SOMA_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError("Canonical NY Fed SOMA registry projection drifted")

    datasets = tuple(
        replace(item, collector_ids=historical_collectors[item.id])
        if item.id in historical_collectors
        else item
        for item in registry.datasets
    )
    collectors = tuple(
        item
        for item in registry.collectors
        if str(item["id"]) != _NYFED_SOMA_COLLECTOR_ID
    )
    return replace(
        registry,
        registry_version="2.25.0",
        datasets=datasets,
        collectors=collectors,
        raw=raw,
        source_sha256=_PRE_NYFED_SOMA_REGISTRY_SOURCE_SHA256,
    )

def nyfed_repo_facilities_registry_profile(registry: Registry) -> Registry:
    """Project the NY Fed repo-facility collector back to exact registry 2.24."""

    registry = market_return_v2_registry_profile(registry)

    version = (registry.schema_version, registry.registry_version)
    if version in {
        ("1.8.0", "2.31.0"),
        ("1.8.0", "2.30.0"),
        ("1.8.0", "2.29.0"),
        ("1.8.0", "2.28.0"),
        ("1.8.0", "2.27.0"),
        ("1.8.0", "2.26.0"),
    }:
        registry = nyfed_soma_summary_registry_profile(registry)
        version = (registry.schema_version, registry.registry_version)
    dataset_by_id = {item.id: item for item in registry.datasets}
    target_datasets = tuple(
        dataset_by_id.get(dataset_id)
        for dataset_id in _NYFED_REPO_FACILITIES_DATASET_IDS
    )
    if any(item is None for item in target_datasets):
        raise RegistryError("NY Fed repo-facility dataset declarations drifted")

    if version == ("1.8.0", "2.24.0"):
        payload = (
            json.dumps(registry.raw, ensure_ascii=True, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8")
        if (
            registry.source_sha256
            != _PRE_NYFED_REPO_FACILITIES_REGISTRY_SOURCE_SHA256
            or hashlib.sha256(payload).hexdigest()
            != _PRE_NYFED_REPO_FACILITIES_REGISTRY_SOURCE_SHA256
            or len(registry.migrations) != 38
            or len(registry.datasets) != 51
            or len(registry.collectors) != 39
            or _NYFED_REPO_FACILITIES_COLLECTOR_ID
            in {str(item["id"]) for item in registry.collectors}
            or any(
                _NYFED_REPO_FACILITIES_COLLECTOR_ID in item.collector_ids
                for item in target_datasets
                if item is not None
            )
        ):
            raise RegistryError(
                "Historical pre-NY-Fed-repo registry profile drifted"
            )
        return registry

    expected_collector = {
        "configuration_env": [],
        "handler": "macro.nyfed_repo_facility_usage_history",
        "id": _NYFED_REPO_FACILITIES_COLLECTOR_ID,
        "input_datasets": [],
        "mutation_policy": {
            "mode": "append_versions_and_snapshot_membership",
            "unchanged": "zero_persistent_writes",
        },
        "network": True,
        "output_datasets": list(_NYFED_REPO_FACILITIES_DATASET_IDS),
        "physical_locks": "derived_from_output_store_paths",
        "retry_policy": {
            "backoff": "none_single_attempt",
            "honor_retry_after": False,
            "max_attempts": 1,
            "transient_classes": [],
        },
        "schedule_eligibility": {"mode": "manual_only"},
        "semantic_identity": {
            "excludes": [
                "captured_at",
                "explicit_small_value_exercises",
                "http_headers",
                "source_row_order",
                "unknown_provider_fields",
            ],
            "includes": [
                "request_scope",
                "normalization_version",
                "normalized_daily_facility_batch",
            ],
        },
        "version": "1.0.0",
        "workload_bounds": {
            "max_bytes": 16_777_216,
            "max_requests": 1,
            "max_rows": 20_000,
            "max_seconds": 60,
        },
    }
    collector_matches = tuple(
        item
        for item in registry.collectors
        if str(item["id"]) == _NYFED_REPO_FACILITIES_COLLECTOR_ID
    )
    historical_collectors: dict[str, tuple[str, ...]] = {}
    for dataset_id, dataset in zip(
        _NYFED_REPO_FACILITIES_DATASET_IDS,
        target_datasets,
        strict=True,
    ):
        assert dataset is not None
        if (
            not dataset.collector_ids
            or dataset.collector_ids[-1]
            != _NYFED_REPO_FACILITIES_COLLECTOR_ID
            or dataset.collector_ids.count(
                _NYFED_REPO_FACILITIES_COLLECTOR_ID
            )
            != 1
        ):
            raise RegistryError("NY Fed repo-facility dataset binding drifted")
        historical_collectors[dataset_id] = dataset.collector_ids[:-1]

    if (
        version != ("1.8.0", "2.25.0")
        or len(registry.migrations) != 38
        or len(registry.datasets) != 51
        or len(registry.collectors) != 40
        or len(collector_matches) != 1
        or dict(collector_matches[0]) != expected_collector
        or any(
            step.collector_id == _NYFED_REPO_FACILITIES_COLLECTOR_ID
            for job in registry.jobs
            for step in job.steps
        )
    ):
        raise RegistryError("Canonical registry cannot reproduce revision 2.24")

    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.24.0"
    raw["collectors"] = [
        item
        for item in raw["collectors"]
        if item["id"] != _NYFED_REPO_FACILITIES_COLLECTOR_ID
    ]
    raw_target_ids: set[str] = set()
    for item in raw["datasets"]:
        dataset_id = item.get("id")
        if dataset_id not in historical_collectors:
            continue
        if (
            tuple(item.get("collector_ids", ()))
            != historical_collectors[dataset_id]
            + (_NYFED_REPO_FACILITIES_COLLECTOR_ID,)
        ):
            raise RegistryError("Canonical NY Fed repo raw binding drifted")
        item["collector_ids"] = list(historical_collectors[dataset_id])
        raw_target_ids.add(str(dataset_id))
    if raw_target_ids != set(_NYFED_REPO_FACILITIES_DATASET_IDS):
        raise RegistryError("Canonical NY Fed repo dataset inventory drifted")

    projected_payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(projected_payload).hexdigest()
        != _PRE_NYFED_REPO_FACILITIES_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError("Canonical NY Fed repo registry projection drifted")

    datasets = tuple(
        replace(item, collector_ids=historical_collectors[item.id])
        if item.id in historical_collectors
        else item
        for item in registry.datasets
    )
    collectors = tuple(
        item
        for item in registry.collectors
        if str(item["id"]) != _NYFED_REPO_FACILITIES_COLLECTOR_ID
    )
    return replace(
        registry,
        registry_version="2.24.0",
        datasets=datasets,
        collectors=collectors,
        raw=raw,
        source_sha256=_PRE_NYFED_REPO_FACILITIES_REGISTRY_SOURCE_SHA256,
    )


def nyfed_overnight_rates_registry_profile(registry: Registry) -> Registry:
    """Project the NY Fed collector back to exact registry 2.23."""

    registry = market_return_v2_registry_profile(registry)

    version = (registry.schema_version, registry.registry_version)
    if version in {
        ("1.8.0", "2.31.0"),
        ("1.8.0", "2.30.0"),
        ("1.8.0", "2.29.0"),
        ("1.8.0", "2.28.0"),
        ("1.8.0", "2.27.0"),
        ("1.8.0", "2.26.0"),
        ("1.8.0", "2.25.0"),
    }:
        registry = nyfed_repo_facilities_registry_profile(registry)
        version = (registry.schema_version, registry.registry_version)
    dataset_by_id = {item.id: item for item in registry.datasets}
    target_datasets = tuple(
        dataset_by_id.get(dataset_id)
        for dataset_id in _NYFED_OVERNIGHT_RATES_DATASET_IDS
    )
    if any(item is None for item in target_datasets):
        raise RegistryError("NY Fed overnight-rate dataset declarations drifted")

    if version == ("1.8.0", "2.23.0"):
        payload = (
            json.dumps(registry.raw, ensure_ascii=True, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8")
        if (
            registry.source_sha256
            != _PRE_NYFED_OVERNIGHT_RATES_REGISTRY_SOURCE_SHA256
            or hashlib.sha256(payload).hexdigest()
            != _PRE_NYFED_OVERNIGHT_RATES_REGISTRY_SOURCE_SHA256
            or len(registry.migrations) != 38
            or len(registry.datasets) != 51
            or len(registry.collectors) != 38
            or _NYFED_OVERNIGHT_RATES_COLLECTOR_ID
            in {str(item["id"]) for item in registry.collectors}
            or any(
                _NYFED_OVERNIGHT_RATES_COLLECTOR_ID in item.collector_ids
                for item in target_datasets
                if item is not None
            )
        ):
            raise RegistryError("Historical pre-NY-Fed registry profile drifted")
        return registry

    expected_collector = {
        "configuration_env": [],
        "handler": "macro.nyfed_overnight_rates_history",
        "id": _NYFED_OVERNIGHT_RATES_COLLECTOR_ID,
        "input_datasets": [],
        "mutation_policy": {
            "mode": "append_versions_and_snapshot_membership",
            "unchanged": "zero_persistent_writes",
        },
        "network": True,
        "output_datasets": list(_NYFED_OVERNIGHT_RATES_DATASET_IDS),
        "physical_locks": "derived_from_output_store_paths",
        "retry_policy": {
            "backoff": "none_single_attempt",
            "honor_retry_after": False,
            "max_attempts": 1,
            "transient_classes": [],
        },
        "schedule_eligibility": {"mode": "manual_only"},
        "semantic_identity": {
            "excludes": [
                "captured_at",
                "http_headers",
                "source_row_order",
                "unknown_provider_fields",
                "unsupported_rate_types",
            ],
            "includes": [
                "request_scope",
                "normalization_version",
                "normalized_headline_rate_batch",
            ],
        },
        "version": "1.0.0",
        "workload_bounds": {
            "max_bytes": 16_777_216,
            "max_requests": 1,
            "max_rows": 20_000,
            "max_seconds": 60,
        },
    }
    collector_matches = tuple(
        item
        for item in registry.collectors
        if str(item["id"]) == _NYFED_OVERNIGHT_RATES_COLLECTOR_ID
    )
    historical_collectors: dict[str, tuple[str, ...]] = {}
    for dataset_id, dataset in zip(
        _NYFED_OVERNIGHT_RATES_DATASET_IDS,
        target_datasets,
        strict=True,
    ):
        assert dataset is not None
        if (
            not dataset.collector_ids
            or dataset.collector_ids[-1] != _NYFED_OVERNIGHT_RATES_COLLECTOR_ID
            or dataset.collector_ids.count(_NYFED_OVERNIGHT_RATES_COLLECTOR_ID)
            != 1
        ):
            raise RegistryError("NY Fed overnight-rate dataset binding drifted")
        historical_collectors[dataset_id] = dataset.collector_ids[:-1]

    if (
        version != ("1.8.0", "2.24.0")
        or len(registry.migrations) != 38
        or len(registry.datasets) != 51
        or len(registry.collectors) != 39
        or len(collector_matches) != 1
        or dict(collector_matches[0]) != expected_collector
        or any(
            step.collector_id == _NYFED_OVERNIGHT_RATES_COLLECTOR_ID
            for job in registry.jobs
            for step in job.steps
        )
    ):
        raise RegistryError("Canonical registry cannot reproduce revision 2.23")

    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.23.0"
    raw["collectors"] = [
        item
        for item in raw["collectors"]
        if item["id"] != _NYFED_OVERNIGHT_RATES_COLLECTOR_ID
    ]
    raw_target_ids: set[str] = set()
    for item in raw["datasets"]:
        dataset_id = item.get("id")
        if dataset_id not in historical_collectors:
            continue
        if (
            tuple(item.get("collector_ids", ()))
            != historical_collectors[dataset_id]
            + (_NYFED_OVERNIGHT_RATES_COLLECTOR_ID,)
        ):
            raise RegistryError("Canonical NY Fed raw binding drifted")
        item["collector_ids"] = list(historical_collectors[dataset_id])
        raw_target_ids.add(str(dataset_id))
    if raw_target_ids != set(_NYFED_OVERNIGHT_RATES_DATASET_IDS):
        raise RegistryError("Canonical NY Fed dataset inventory drifted")

    projected_payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(projected_payload).hexdigest()
        != _PRE_NYFED_OVERNIGHT_RATES_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError("Canonical NY Fed registry projection drifted")

    datasets = tuple(
        replace(item, collector_ids=historical_collectors[item.id])
        if item.id in historical_collectors
        else item
        for item in registry.datasets
    )
    collectors = tuple(
        item
        for item in registry.collectors
        if str(item["id"]) != _NYFED_OVERNIGHT_RATES_COLLECTOR_ID
    )
    return replace(
        registry,
        registry_version="2.23.0",
        datasets=datasets,
        collectors=collectors,
        raw=raw,
        source_sha256=_PRE_NYFED_OVERNIGHT_RATES_REGISTRY_SOURCE_SHA256,
    )


def fmp_treasury_yield_curve_registry_profile(registry: Registry) -> Registry:
    """Project the FMP Treasury collector back to exact registry 2.21."""

    registry = market_return_v2_registry_profile(registry)

    version = (registry.schema_version, registry.registry_version)
    if version in {
        ("1.8.0", "2.31.0"),
        ("1.8.0", "2.30.0"),
        ("1.8.0", "2.29.0"),
        ("1.8.0", "2.28.0"),
        ("1.8.0", "2.27.0"),
        ("1.8.0", "2.26.0"),
        ("1.8.0", "2.25.0"),
        ("1.8.0", "2.24.0"),
    }:
        registry = nyfed_overnight_rates_registry_profile(registry)
        version = (registry.schema_version, registry.registry_version)
    dataset_by_id = {item.id: item for item in registry.datasets}
    target_datasets = tuple(
        dataset_by_id.get(dataset_id)
        for dataset_id in _FMP_TREASURY_YIELD_CURVE_DATASET_IDS
    )
    if any(item is None for item in target_datasets):
        raise RegistryError("FMP Treasury dataset declarations drifted")

    if version == ("1.8.0", "2.21.0"):
        payload = (
            json.dumps(registry.raw, ensure_ascii=True, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8")
        if (
            registry.source_sha256
            != _PRE_FMP_TREASURY_YIELD_CURVE_REGISTRY_SOURCE_SHA256
            or hashlib.sha256(payload).hexdigest()
            != _PRE_FMP_TREASURY_YIELD_CURVE_REGISTRY_SOURCE_SHA256
            or registry.raw.get("registry_version") != "2.21.0"
            or len(registry.migrations) != 38
            or len(registry.datasets) != 51
            or len(registry.collectors) != 37
            or _FMP_TREASURY_YIELD_CURVE_COLLECTOR_ID
            in {str(item["id"]) for item in registry.collectors}
            or any(
                _FMP_TREASURY_YIELD_CURVE_COLLECTOR_ID in item.collector_ids
                for item in target_datasets
                if item is not None
            )
            or registry.store("macro").migration_order[-1]
            != _FMP_WHOLESALE_CALENDAR_MIGRATION_ID
        ):
            raise RegistryError(
                "Historical pre-FMP-Treasury registry profile drifted"
            )
        return registry

    expected_collector = {
        "configuration_env": ["FMP_API_KEY"],
        "handler": "macro.fmp_treasury_yield_curve_history",
        "id": _FMP_TREASURY_YIELD_CURVE_COLLECTOR_ID,
        "input_datasets": [],
        "mutation_policy": {
            "mode": "append_versions_and_snapshot_membership",
            "unchanged": "zero_persistent_writes",
        },
        "network": True,
        "output_datasets": list(_FMP_TREASURY_YIELD_CURVE_DATASET_IDS),
        "physical_locks": "derived_from_output_store_paths",
        "retry_policy": {
            "backoff": "none_single_attempt",
            "honor_retry_after": False,
            "max_attempts": 1,
            "transient_classes": [],
        },
        "schedule_eligibility": {"mode": "manual_only"},
        "semantic_identity": {
            "excludes": [
                "api_key",
                "captured_at",
                "http_headers",
                "source_row_order",
                "unknown_provider_fields",
            ],
            "includes": [
                "request_scope",
                "normalization_version",
                "normalized_12_tenor_batch",
            ],
        },
        "version": "1.0.0",
        "workload_bounds": {
            "max_bytes": 16_777_216,
            "max_requests": 1,
            "max_rows": 20_000,
            "max_seconds": 60,
        },
    }
    collector_matches = tuple(
        item
        for item in registry.collectors
        if str(item["id"]) == _FMP_TREASURY_YIELD_CURVE_COLLECTOR_ID
    )
    historical_collectors: dict[str, tuple[str, ...]] = {}
    for dataset_id, dataset in zip(
        _FMP_TREASURY_YIELD_CURVE_DATASET_IDS,
        target_datasets,
        strict=True,
    ):
        assert dataset is not None
        if (
            not dataset.collector_ids
            or dataset.collector_ids[-1]
            != _FMP_TREASURY_YIELD_CURVE_COLLECTOR_ID
            or dataset.collector_ids.count(
                _FMP_TREASURY_YIELD_CURVE_COLLECTOR_ID
            )
            != 1
        ):
            raise RegistryError("FMP Treasury dataset collector binding drifted")
        historical_collectors[dataset_id] = dataset.collector_ids[:-1]

    if (
        version != ("1.8.0", "2.23.0")
        or len(registry.migrations) != 38
        or len(registry.datasets) != 51
        or len(registry.collectors) != 38
        or len(collector_matches) != 1
        or dict(collector_matches[0]) != expected_collector
        or registry.store("macro").migration_order[-1]
        != _FMP_WHOLESALE_CALENDAR_MIGRATION_ID
        or any(
            step.collector_id == _FMP_TREASURY_YIELD_CURVE_COLLECTOR_ID
            for job in registry.jobs
            for step in job.steps
        )
    ):
        raise RegistryError("Canonical registry cannot reproduce revision 2.21")

    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.21.0"
    raw["collectors"] = [
        item
        for item in raw["collectors"]
        if item["id"] != _FMP_TREASURY_YIELD_CURVE_COLLECTOR_ID
    ]
    raw_target_ids: set[str] = set()
    for item in raw["datasets"]:
        dataset_id = item.get("id")
        if dataset_id not in historical_collectors:
            continue
        if (
            tuple(item.get("collector_ids", ()))
            != historical_collectors[dataset_id]
            + (_FMP_TREASURY_YIELD_CURVE_COLLECTOR_ID,)
        ):
            raise RegistryError("Canonical FMP Treasury raw binding drifted")
        item["collector_ids"] = list(historical_collectors[dataset_id])
        raw_target_ids.add(str(dataset_id))
    if raw_target_ids != set(_FMP_TREASURY_YIELD_CURVE_DATASET_IDS):
        raise RegistryError("Canonical FMP Treasury dataset inventory drifted")

    projected_payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(projected_payload).hexdigest()
        != _PRE_FMP_TREASURY_YIELD_CURVE_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError("Canonical FMP Treasury projection drifted")

    datasets = tuple(
        replace(item, collector_ids=historical_collectors[item.id])
        if item.id in historical_collectors
        else item
        for item in registry.datasets
    )
    collectors = tuple(
        item
        for item in registry.collectors
        if str(item["id"]) != _FMP_TREASURY_YIELD_CURVE_COLLECTOR_ID
    )
    return replace(
        registry,
        registry_version="2.21.0",
        datasets=datasets,
        collectors=collectors,
        raw=raw,
        source_sha256=_PRE_FMP_TREASURY_YIELD_CURVE_REGISTRY_SOURCE_SHA256,
    )


def fmp_wholesale_calendar_registry_profile(registry: Registry) -> Registry:
    """Project wholesale FMP calendar evidence back to the exact 2.20 registry."""

    registry = market_return_v2_registry_profile(registry)

    version = (registry.schema_version, registry.registry_version)
    if version in {
        ("1.8.0", "2.31.0"),
        ("1.8.0", "2.30.0"),
        ("1.8.0", "2.29.0"),
        ("1.8.0", "2.28.0"),
        ("1.8.0", "2.27.0"),
        ("1.8.0", "2.26.0"),
        ("1.8.0", "2.25.0"),
        ("1.8.0", "2.24.0"),
        ("1.8.0", "2.23.0"),
    }:
        registry = fmp_treasury_yield_curve_registry_profile(registry)
        version = (registry.schema_version, registry.registry_version)
    if version == ("1.8.0", "2.20.0"):
        payload = (
            json.dumps(registry.raw, ensure_ascii=True, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8")
        if (
            registry.source_sha256
            != _PRE_FMP_WHOLESALE_CALENDAR_REGISTRY_SOURCE_SHA256
            or hashlib.sha256(payload).hexdigest()
            != _PRE_FMP_WHOLESALE_CALENDAR_REGISTRY_SOURCE_SHA256
            or registry.raw.get("registry_version") != "2.20.0"
            or len(registry.migrations) != 37
            or len(registry.datasets) != 50
            or len(registry.collectors) != 36
            or _FMP_WHOLESALE_CALENDAR_MIGRATION_ID
            in {item.id for item in registry.migrations}
            or _FMP_WHOLESALE_CALENDAR_DATASET_ID
            in {item.id for item in registry.datasets}
            or _FMP_WHOLESALE_CALENDAR_COLLECTOR_ID
            in {str(item["id"]) for item in registry.collectors}
            or registry.store("macro").migration_order[-1]
            != _MACRO_HISTORY_MIGRATION_ID
        ):
            raise RegistryError("Historical pre-FMP-wholesale registry profile drifted")
        return registry

    expected_migration_raw = {
        "dependencies": [_MACRO_HISTORY_MIGRATION_ID],
        "id": _FMP_WHOLESALE_CALENDAR_MIGRATION_ID,
        "ordinal": 16,
        "reconstruction_state": "fixture_validated",
        "resource": "quant_data/migrations/macro/0016_fmp_calendar_wholesale_evidence.sql",
        "semantic_scope": "Bounded FMP U.S. economic-calendar wholesale immutable evidence capture and raw-row lineage for local replay of reviewed consensus aliases.",
        "sha256": "78dc02d34c0489c3f1fe4b7847870a18955606b1f47a3309ed8464ee9f3bbb4d",
        "store": "macro",
    }
    expected_dataset = {
        "active": True,
        "collector_ids": [_FMP_WHOLESALE_CALENDAR_COLLECTOR_ID],
        "dashboard_ids": [],
        "export_ids": [],
        "freshness": {
            "cadence": "manual",
            "expected_lag": "P0D",
            "health_severity": "warning",
            "if_new": True,
            "measured_from": "successful_capture",
            "stale_after": "P30D",
        },
        "id": _FMP_WHOLESALE_CALENDAR_DATASET_ID,
        "identity": {
            "stable_fields": [
                "request_country", "request_start_date", "request_end_date"
            ],
            "version_fields": [
                "capture_id", "response_sha256", "semantic_identity"
            ],
        },
        "layer": "evidence",
        "physical": {"relations": [
            {"kind": "table", "name": _FMP_WHOLESALE_CALENDAR_RELATIONS[0]},
            {"kind": "table", "name": _FMP_WHOLESALE_CALENDAR_RELATIONS[1]},
        ]},
        "quality_contract": {
            "missingness": "not_applicable",
            "required_warnings": [],
            "rules": [
                "raw_response_retained_private",
                "complete_us_calendar_batch",
                "source_row_lineage",
                "semantic_replay_no_write",
            ],
            "units": "source_declared",
        },
        "revision_policy": "immutable_capture",
        "store": "macro",
        "temporal": {
            "availability_fields": ["captured_at"],
            "availability_precision": "datetime",
            "history_basis": "local_capture",
            "missingness": "not_applicable",
            "observation_fields": ["request_start_date", "request_end_date"],
            "observation_precision": "date",
            "range_semantics": "inclusive",
            "timezone_rule": "source_native_no_conversion",
            "vintage_modes": ["latest", "as_of"],
        },
        "tool_ids": [],
        "version": "1.0.0",
    }
    expected_collector = {
        "configuration_env": ["FMP_API_KEY"],
        "handler": "macro.fmp_us_economic_calendar_wholesale",
        "id": _FMP_WHOLESALE_CALENDAR_COLLECTOR_ID,
        "input_datasets": [],
        "mutation_policy": {
            "mode": "append_immutable_captures_and_rows",
            "unchanged": "zero_persistent_writes",
        },
        "network": True,
        "output_datasets": [_FMP_WHOLESALE_CALENDAR_DATASET_ID],
        "physical_locks": "derived_from_output_store_paths",
        "retry_policy": {
            "backoff": "none_single_attempt",
            "honor_retry_after": False,
            "max_attempts": 1,
            "transient_classes": [],
        },
        "schedule_eligibility": {"mode": "manual_only"},
        "semantic_identity": {
            "excludes": [
                "api_key", "captured_at", "http_headers", "source_row_order"
            ],
            "includes": [
                "request_scope", "normalization_version", "normalized_complete_batch"
            ],
        },
        "version": "1.0.0",
        "workload_bounds": {
            "max_bytes": 1_048_576,
            "max_requests": 1,
            "max_rows": 2_000,
            "max_seconds": 60,
        },
    }
    expected_migration = MigrationDeclaration(
        _FMP_WHOLESALE_CALENDAR_MIGRATION_ID,
        "macro",
        16,
        expected_migration_raw["resource"],
        expected_migration_raw["sha256"],
        expected_migration_raw["semantic_scope"],
        (_MACRO_HISTORY_MIGRATION_ID,),
        "fixture_validated",
    )
    migration_matches = tuple(
        item for item in registry.migrations
        if item.id == _FMP_WHOLESALE_CALENDAR_MIGRATION_ID
    )
    dataset_matches = tuple(
        item for item in registry.datasets
        if item.id == _FMP_WHOLESALE_CALENDAR_DATASET_ID
    )
    collector_matches = tuple(
        item for item in registry.collectors
        if str(item["id"]) == _FMP_WHOLESALE_CALENDAR_COLLECTOR_ID
    )
    raw_migration_matches = [
        item for item in registry.raw["migrations"]
        if item.get("id") == _FMP_WHOLESALE_CALENDAR_MIGRATION_ID
    ]
    raw_dataset_matches = [
        item for item in registry.raw["datasets"]
        if item.get("id") == _FMP_WHOLESALE_CALENDAR_DATASET_ID
    ]
    raw_collector_matches = [
        item for item in registry.raw["collectors"]
        if item.get("id") == _FMP_WHOLESALE_CALENDAR_COLLECTOR_ID
    ]
    raw_macro_stores = [
        item for item in registry.raw["stores"] if item.get("id") == "macro"
    ]
    if (
        version != ("1.8.0", "2.21.0")
        or len(registry.migrations) != 38
        or len(registry.datasets) != 51
        or len(registry.collectors) != 37
        or len(migration_matches) != 1
        or migration_matches[0] != expected_migration
        or len(dataset_matches) != 1
        or dataset_matches[0].store != "macro"
        or dataset_matches[0].layer != "evidence"
        or dataset_matches[0].relations != _FMP_WHOLESALE_CALENDAR_RELATIONS
        or dataset_matches[0].collector_ids != (_FMP_WHOLESALE_CALENDAR_COLLECTOR_ID,)
        or dataset_matches[0].tool_ids
        or dataset_matches[0].dashboard_ids
        or dataset_matches[0].export_ids
        or len(collector_matches) != 1
        or dict(collector_matches[0]) != expected_collector
        or tuple(registry.store("macro").migration_order[-2:])
        != (_MACRO_HISTORY_MIGRATION_ID, _FMP_WHOLESALE_CALENDAR_MIGRATION_ID)
        or len(raw_migration_matches) != 1
        or dict(raw_migration_matches[0]) != expected_migration_raw
        or len(raw_dataset_matches) != 1
        or dict(raw_dataset_matches[0]) != expected_dataset
        or len(raw_collector_matches) != 1
        or dict(raw_collector_matches[0]) != expected_collector
        or len(raw_macro_stores) != 1
        or tuple(raw_macro_stores[0].get("migration_order", ())[-2:])
        != (_MACRO_HISTORY_MIGRATION_ID, _FMP_WHOLESALE_CALENDAR_MIGRATION_ID)
        or any(
            step.collector_id == _FMP_WHOLESALE_CALENDAR_COLLECTOR_ID
            for job in registry.jobs
            for step in job.steps
        )
    ):
        raise RegistryError("Canonical registry cannot reproduce revision 2.20")

    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.20.0"
    raw["migrations"] = [
        item for item in raw["migrations"]
        if item["id"] != _FMP_WHOLESALE_CALENDAR_MIGRATION_ID
    ]
    raw["datasets"] = [
        item for item in raw["datasets"]
        if item["id"] != _FMP_WHOLESALE_CALENDAR_DATASET_ID
    ]
    raw["collectors"] = [
        item for item in raw["collectors"]
        if item["id"] != _FMP_WHOLESALE_CALENDAR_COLLECTOR_ID
    ]
    raw_macro_stores = [
        item for item in raw["stores"] if item.get("id") == "macro"
    ]
    if len(raw_macro_stores) != 1:
        raise RegistryError("Canonical FMP wholesale macro-store binding drifted")
    raw_macro_stores[0]["migration_order"] = [
        migration_id for migration_id in raw_macro_stores[0]["migration_order"]
        if migration_id != _FMP_WHOLESALE_CALENDAR_MIGRATION_ID
    ]
    projected_payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if hashlib.sha256(projected_payload).hexdigest() != _PRE_FMP_WHOLESALE_CALENDAR_REGISTRY_SOURCE_SHA256:
        raise RegistryError("Canonical FMP wholesale calendar projection drifted")

    stores = tuple(
        replace(
            store,
            migration_order=tuple(
                migration_id for migration_id in store.migration_order
                if migration_id != _FMP_WHOLESALE_CALENDAR_MIGRATION_ID
            ),
        ) if store.id == "macro" else store
        for store in registry.stores
    )
    migrations = tuple(
        item for item in registry.migrations
        if item.id != _FMP_WHOLESALE_CALENDAR_MIGRATION_ID
    )
    datasets = tuple(
        item for item in registry.datasets
        if item.id != _FMP_WHOLESALE_CALENDAR_DATASET_ID
    )
    collectors = tuple(
        item for item in registry.collectors
        if str(item["id"]) != _FMP_WHOLESALE_CALENDAR_COLLECTOR_ID
    )
    return replace(
        registry,
        registry_version="2.20.0",
        stores=stores,
        migrations=migrations,
        datasets=datasets,
        collectors=collectors,
        raw=raw,
        source_sha256=_PRE_FMP_WHOLESALE_CALENDAR_REGISTRY_SOURCE_SHA256,
    )


def fmp_employment_release_calendar_registry_profile(registry: Registry) -> Registry:
    """Project the employment FMP calendar refresh collector back to 2.19."""

    registry = market_return_v2_registry_profile(registry)

    version = (registry.schema_version, registry.registry_version)
    if version in {
        ("1.8.0", "2.31.0"),
        ("1.8.0", "2.30.0"),
        ("1.8.0", "2.29.0"),
        ("1.8.0", "2.28.0"),
        ("1.8.0", "2.27.0"),
        ("1.8.0", "2.26.0"),
        ("1.8.0", "2.25.0"),
        ("1.8.0", "2.24.0"),
        ("1.8.0", "2.23.0"),
        ("1.8.0", "2.21.0"),
    }:
        registry = fmp_wholesale_calendar_registry_profile(registry)
        version = (registry.schema_version, registry.registry_version)
    calendar_matches = tuple(
        item
        for item in registry.datasets
        if item.id == _FMP_GDP_CPI_CALENDAR_DATASET_ID
    )
    if len(calendar_matches) != 1:
        raise RegistryError("FMP employment calendar dataset declaration drifted")
    calendar = calendar_matches[0]
    expected_calendar_collectors = (
        "fixture.macro.calendar_import",
        _FMP_GDP_CPI_CALENDAR_COLLECTOR_ID,
        _FMP_EMPLOYMENT_CALENDAR_COLLECTOR_ID,
    )
    historical_calendar_collectors = expected_calendar_collectors[:-1]
    if version == ("1.8.0", "2.19.0"):
        if (
            registry.source_sha256
            != _PRE_FMP_EMPLOYMENT_CALENDAR_REGISTRY_SOURCE_SHA256
            or registry.raw.get("registry_version") != "2.19.0"
            or len(registry.migrations) != 37
            or len(registry.datasets) != 50
            or len(registry.collectors) != 35
            or _FMP_EMPLOYMENT_CALENDAR_COLLECTOR_ID
            in {str(item["id"]) for item in registry.collectors}
            or calendar.collector_ids != historical_calendar_collectors
        ):
            raise RegistryError(
                "Historical pre-FMP-employment-calendar registry profile drifted"
            )
        return registry

    collector_matches = tuple(
        item
        for item in registry.collectors
        if str(item["id"]) == _FMP_EMPLOYMENT_CALENDAR_COLLECTOR_ID
    )
    expected_collector = {
        "configuration_env": ["FMP_API_KEY"],
        "handler": "macro.fmp_employment_release_calendar_refresh",
        "id": _FMP_EMPLOYMENT_CALENDAR_COLLECTOR_ID,
        "input_datasets": [],
        "mutation_policy": {
            "mode": "append_versions_and_snapshot_membership",
            "unchanged": "zero_persistent_writes",
        },
        "network": True,
        "output_datasets": [_FMP_GDP_CPI_CALENDAR_DATASET_ID],
        "physical_locks": "derived_from_output_store_paths",
        "retry_policy": {
            "backoff": "none_single_attempt",
            "honor_retry_after": False,
            "max_attempts": 1,
            "transient_classes": [],
        },
        "schedule_eligibility": {"mode": "manual_only"},
        "semantic_identity": {
            "excludes": [
                "api_key",
                "captured_at",
                "http_headers",
                "source_row_order",
            ],
            "includes": [
                "request_scope",
                "normalization_version",
                "normalized_complete_batch",
            ],
        },
        "version": "1.0.0",
        "workload_bounds": {
            "max_bytes": 1_048_576,
            "max_requests": 1,
            "max_rows": 2_000,
            "max_seconds": 60,
        },
    }
    if (
        version != ("1.8.0", "2.20.0")
        or len(registry.migrations) != 37
        or len(registry.datasets) != 50
        or len(registry.collectors) != 36
        or len(collector_matches) != 1
        or dict(collector_matches[0]) != expected_collector
        or calendar.collector_ids != expected_calendar_collectors
        or any(
            step.collector_id == _FMP_EMPLOYMENT_CALENDAR_COLLECTOR_ID
            for job in registry.jobs
            for step in job.steps
        )
    ):
        raise RegistryError("Canonical registry cannot reproduce revision 2.19")

    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.19.0"
    raw["collectors"] = [
        item
        for item in raw["collectors"]
        if item["id"] != _FMP_EMPLOYMENT_CALENDAR_COLLECTOR_ID
    ]
    raw_calendar = [
        item
        for item in raw["datasets"]
        if item.get("id") == _FMP_GDP_CPI_CALENDAR_DATASET_ID
    ]
    if (
        len(raw_calendar) != 1
        or tuple(raw_calendar[0].get("collector_ids", ()))
        != expected_calendar_collectors
    ):
        raise RegistryError("Canonical FMP employment calendar binding drifted")
    raw_calendar[0]["collector_ids"] = list(historical_calendar_collectors)
    projected_payload = (
        json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(projected_payload).hexdigest()
        != _PRE_FMP_EMPLOYMENT_CALENDAR_REGISTRY_SOURCE_SHA256
    ):
        raise RegistryError("Canonical FMP employment calendar projection drifted")

    collectors = tuple(
        item
        for item in registry.collectors
        if str(item["id"]) != _FMP_EMPLOYMENT_CALENDAR_COLLECTOR_ID
    )
    datasets = tuple(
        replace(item, collector_ids=historical_calendar_collectors)
        if item.id == _FMP_GDP_CPI_CALENDAR_DATASET_ID
        else item
        for item in registry.datasets
    )
    return replace(
        registry,
        registry_version="2.19.0",
        datasets=datasets,
        collectors=collectors,
        raw=raw,
        source_sha256=_PRE_FMP_EMPLOYMENT_CALENDAR_REGISTRY_SOURCE_SHA256,
    )

def fmp_gdp_cpi_release_calendar_registry_profile(registry: Registry) -> Registry:
    """Project the manual FMP GDP/CPI calendar collector back to 2.18."""

    registry = market_return_v2_registry_profile(registry)

    version = (registry.schema_version, registry.registry_version)
    if version in {
        ("1.8.0", "2.31.0"),
        ("1.8.0", "2.30.0"),
        ("1.8.0", "2.29.0"),
        ("1.8.0", "2.28.0"),
        ("1.8.0", "2.27.0"),
        ("1.8.0", "2.26.0"),
        ("1.8.0", "2.25.0"),
        ("1.8.0", "2.24.0"),
        ("1.8.0", "2.20.0"),
        ("1.8.0", "2.21.0"),
        ("1.8.0", "2.23.0"),
    }:
        registry = fmp_employment_release_calendar_registry_profile(registry)
        version = (registry.schema_version, registry.registry_version)
    calendar_matches = tuple(
        item
        for item in registry.datasets
        if item.id == _FMP_GDP_CPI_CALENDAR_DATASET_ID
    )
    if len(calendar_matches) != 1:
        raise RegistryError("FMP GDP/CPI calendar dataset declaration drifted")
    calendar = calendar_matches[0]
    official_matches = tuple(
        item
        for item in registry.datasets
        if item.id == _FMP_GDP_CPI_OFFICIAL_DATASET_ID
    )
    surprise_tool_matches = tuple(
        item
        for item in registry.tools
        if item["id"] == _FMP_GDP_CPI_SURPRISE_TOOL_ID
    )
    if len(official_matches) != 1 or len(surprise_tool_matches) != 1:
        raise RegistryError("FMP GDP/CPI surprise dependency declaration drifted")
    official = official_matches[0]
    surprise_tool = surprise_tool_matches[0]
    if version == ("1.8.0", "2.18.0"):
        if (
            registry.source_sha256
            != _PRE_FMP_GDP_CPI_CALENDAR_REGISTRY_SOURCE_SHA256
            or registry.raw.get("registry_version") != "2.18.0"
            or len(registry.migrations) != 37
            or len(registry.datasets) != 50
            or len(registry.collectors) != 34
            or _FMP_GDP_CPI_CALENDAR_COLLECTOR_ID
            in {str(item["id"]) for item in registry.collectors}
            or calendar.collector_ids != ("fixture.macro.calendar_import",)
            or official.tool_ids
            or tuple(surprise_tool["datasets"])
            != (_FMP_GDP_CPI_CALENDAR_DATASET_ID,)
            or surprise_tool["examples"][0]["identifiers"] != ["fixture"]
        ):
            raise RegistryError("Historical pre-FMP-calendar registry profile drifted")
        return registry

    if (
        version != ("1.8.0", "2.19.0")
        or len(registry.migrations) != 37
        or len(registry.datasets) != 50
        or len(registry.collectors) != 35
        or sum(
            str(item["id"]) == _FMP_GDP_CPI_CALENDAR_COLLECTOR_ID
            for item in registry.collectors
        )
        != 1
        or calendar.collector_ids
        != (
            "fixture.macro.calendar_import",
            _FMP_GDP_CPI_CALENDAR_COLLECTOR_ID,
        )
        or official.tool_ids != (_FMP_GDP_CPI_SURPRISE_TOOL_ID,)
        or tuple(surprise_tool["datasets"])
        != (
            _FMP_GDP_CPI_CALENDAR_DATASET_ID,
            _FMP_GDP_CPI_OFFICIAL_DATASET_ID,
        )
        or surprise_tool["examples"][0]["identifiers"]
        != ["us_gdp_real_qoq_saar_advance"]
    ):
        raise RegistryError("Canonical registry cannot reproduce revision 2.18")

    collectors = tuple(
        item
        for item in registry.collectors
        if str(item["id"]) != _FMP_GDP_CPI_CALENDAR_COLLECTOR_ID
    )
    datasets = tuple(
        replace(
            item,
            collector_ids=tuple(
                collector_id
                for collector_id in item.collector_ids
                if collector_id != _FMP_GDP_CPI_CALENDAR_COLLECTOR_ID
            ),
        )
        if item.id == _FMP_GDP_CPI_CALENDAR_DATASET_ID
        else replace(
            item,
            tool_ids=tuple(
                tool_id
                for tool_id in item.tool_ids
                if tool_id != _FMP_GDP_CPI_SURPRISE_TOOL_ID
            ),
        )
        if item.id == _FMP_GDP_CPI_OFFICIAL_DATASET_ID
        else item
        for item in registry.datasets
    )
    projected_surprise_tool = copy.deepcopy(dict(surprise_tool))
    projected_surprise_tool["datasets"] = [
        dataset_id
        for dataset_id in projected_surprise_tool["datasets"]
        if dataset_id != _FMP_GDP_CPI_OFFICIAL_DATASET_ID
    ]
    projected_surprise_tool["examples"][0]["identifiers"] = ["fixture"]
    tools = tuple(
        projected_surprise_tool
        if item["id"] == _FMP_GDP_CPI_SURPRISE_TOOL_ID
        else item
        for item in registry.tools
    )
    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.18.0"
    raw["collectors"] = [
        item
        for item in raw["collectors"]
        if item["id"] != _FMP_GDP_CPI_CALENDAR_COLLECTOR_ID
    ]
    for dataset in raw["datasets"]:
        if dataset.get("id") == _FMP_GDP_CPI_CALENDAR_DATASET_ID:
            dataset["collector_ids"] = [
                collector_id
                for collector_id in dataset["collector_ids"]
                if collector_id != _FMP_GDP_CPI_CALENDAR_COLLECTOR_ID
            ]
        elif dataset.get("id") == _FMP_GDP_CPI_OFFICIAL_DATASET_ID:
            dataset["tool_ids"] = [
                tool_id
                for tool_id in dataset["tool_ids"]
                if tool_id != _FMP_GDP_CPI_SURPRISE_TOOL_ID
            ]
    for tool in raw["tools"]:
        if tool.get("id") == _FMP_GDP_CPI_SURPRISE_TOOL_ID:
            tool["datasets"] = [
                dataset_id
                for dataset_id in tool["datasets"]
                if dataset_id != _FMP_GDP_CPI_OFFICIAL_DATASET_ID
            ]
            tool["examples"][0]["identifiers"] = ["fixture"]
    return replace(
        registry,
        registry_version="2.18.0",
        datasets=datasets,
        collectors=collectors,
        tools=tools,
        raw=raw,
        source_sha256=_PRE_FMP_GDP_CPI_CALENDAR_REGISTRY_SOURCE_SHA256,
    )


def macro_history_extension_registry_profile(registry: Registry) -> Registry:
    """Project the one-time macro-history extension back to registry 2.17."""

    registry = market_return_v2_registry_profile(registry)

    version = (registry.schema_version, registry.registry_version)
    if version in {
        ("1.8.0", "2.31.0"),
        ("1.8.0", "2.30.0"),
        ("1.8.0", "2.29.0"),
        ("1.8.0", "2.28.0"),
        ("1.8.0", "2.27.0"),
        ("1.8.0", "2.26.0"),
        ("1.8.0", "2.25.0"),
        ("1.8.0", "2.24.0"),
        ("1.8.0", "2.19.0"),
        ("1.8.0", "2.20.0"),
        ("1.8.0", "2.21.0"),
        ("1.8.0", "2.23.0"),
    }:
        registry = fmp_gdp_cpi_release_calendar_registry_profile(registry)
        version = (registry.schema_version, registry.registry_version)
    if version == ("1.8.0", "2.17.0"):
        if (
            registry.source_sha256 != _PRE_MACRO_HISTORY_REGISTRY_SOURCE_SHA256
            or registry.raw.get("registry_version") != "2.17.0"
            or len(registry.migrations) != 36
            or len(registry.datasets) != 50
            or len(registry.collectors) != 32
        ):
            raise RegistryError("Historical pre-history-extension registry profile drifted")
        return registry

    if (
        version != ("1.8.0", "2.18.0")
        or len(registry.migrations) != 37
        or len(registry.datasets) != 50
        or len(registry.collectors) != 34
        or registry.store("macro").migration_order[-1]
        != _MACRO_HISTORY_MIGRATION_ID
        or _MACRO_HISTORY_MIGRATION_ID
        not in {item.id for item in registry.migrations}
        or not _MACRO_HISTORY_COLLECTOR_IDS.issubset(
            {str(item["id"]) for item in registry.collectors}
        )
    ):
        raise RegistryError("Canonical registry cannot reproduce revision 2.17")

    expected_collectors = (
        "bea.macro.live_gdp_vintages",
        "bls.macro.live_cpi_vintages",
        "philadelphia_fed.macro.live_employment_vintages",
        "bls.macro.live_employment_current",
        "bls.macro.cpi_current_history",
        "philadelphia_fed.macro.gdp_cpi_vintage_history",
    )
    history_datasets = {
        item.id: item
        for item in registry.datasets
        if item.id in _MACRO_VINTAGE_DATASET_IDS
    }
    if set(history_datasets) != set(_MACRO_VINTAGE_DATASET_IDS) or any(
        tuple(item.collector_ids) != expected_collectors
        for item in history_datasets.values()
    ):
        raise RegistryError("Canonical macro-history dataset bindings drifted")

    migrations = tuple(
        item for item in registry.migrations if item.id != _MACRO_HISTORY_MIGRATION_ID
    )
    collectors = tuple(
        item
        for item in registry.collectors
        if str(item["id"]) not in _MACRO_HISTORY_COLLECTOR_IDS
    )
    datasets = tuple(
        replace(
            item,
            collector_ids=tuple(
                collector_id
                for collector_id in item.collector_ids
                if collector_id not in _MACRO_HISTORY_COLLECTOR_IDS
            ),
        )
        if item.id in _MACRO_VINTAGE_DATASET_IDS
        else item
        for item in registry.datasets
    )
    stores = tuple(
        replace(
            store,
            migration_order=tuple(
                migration_id
                for migration_id in store.migration_order
                if migration_id != _MACRO_HISTORY_MIGRATION_ID
            ),
        )
        for store in registry.stores
    )
    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.17.0"
    raw["migrations"] = [
        item
        for item in raw["migrations"]
        if item["id"] != _MACRO_HISTORY_MIGRATION_ID
    ]
    raw["collectors"] = [
        item
        for item in raw["collectors"]
        if item["id"] not in _MACRO_HISTORY_COLLECTOR_IDS
    ]
    for dataset in raw["datasets"]:
        if dataset.get("id") in _MACRO_VINTAGE_DATASET_IDS:
            dataset["collector_ids"] = [
                collector_id
                for collector_id in dataset["collector_ids"]
                if collector_id not in _MACRO_HISTORY_COLLECTOR_IDS
            ]
    for store in raw["stores"]:
        store["migration_order"] = [
            migration_id
            for migration_id in store["migration_order"]
            if migration_id != _MACRO_HISTORY_MIGRATION_ID
        ]
    return replace(
        registry,
        registry_version="2.17.0",
        stores=stores,
        migrations=migrations,
        datasets=datasets,
        collectors=collectors,
        raw=raw,
        source_sha256=_PRE_MACRO_HISTORY_REGISTRY_SOURCE_SHA256,
    )


def employment_vintage_registry_profile(registry: Registry) -> Registry:
    """Project the employment-vintage revision back to registry 2.16."""

    registry = market_return_v2_registry_profile(registry)

    version = (registry.schema_version, registry.registry_version)
    if version in {
        ("1.8.0", "2.31.0"),
        ("1.8.0", "2.30.0"),
        ("1.8.0", "2.29.0"),
        ("1.8.0", "2.28.0"),
        ("1.8.0", "2.27.0"),
        ("1.8.0", "2.26.0"),
        ("1.8.0", "2.25.0"),
        ("1.8.0", "2.24.0"),
        ("1.8.0", "2.18.0"),
        ("1.8.0", "2.19.0"),
        ("1.8.0", "2.20.0"),
        ("1.8.0", "2.21.0"),
        ("1.8.0", "2.23.0"),
    }:
        registry = macro_history_extension_registry_profile(registry)
        version = (registry.schema_version, registry.registry_version)
    if version == ("1.8.0", "2.16.0"):
        if (
            registry.source_sha256
            != _PRE_EMPLOYMENT_VINTAGE_REGISTRY_SOURCE_SHA256
            or registry.raw.get("registry_version") != "2.16.0"
            or len(registry.migrations) != 35
            or len(registry.datasets) != 50
            or len(registry.collectors) != 30
        ):
            raise RegistryError("Historical pre-employment registry profile drifted")
        return registry

    if (
        version != ("1.8.0", "2.17.0")
        or len(registry.migrations) != 36
        or len(registry.datasets) != 50
        or len(registry.collectors) != 32
        or registry.store("macro").migration_order[-1]
        != _EMPLOYMENT_VINTAGE_MIGRATION_ID
        or _EMPLOYMENT_VINTAGE_MIGRATION_ID
        not in {item.id for item in registry.migrations}
        or not _EMPLOYMENT_VINTAGE_COLLECTOR_IDS.issubset(
            {str(item["id"]) for item in registry.collectors}
        )
    ):
        raise RegistryError("Canonical registry cannot reproduce revision 2.16")

    expected_collectors = (
        "bea.macro.live_gdp_vintages",
        "bls.macro.live_cpi_vintages",
        "philadelphia_fed.macro.live_employment_vintages",
        "bls.macro.live_employment_current",
    )
    employment_datasets = {
        item.id: item
        for item in registry.datasets
        if item.id in _MACRO_VINTAGE_DATASET_IDS
    }
    if set(employment_datasets) != set(_MACRO_VINTAGE_DATASET_IDS) or any(
        tuple(item.collector_ids) != expected_collectors
        for item in employment_datasets.values()
    ):
        raise RegistryError("Canonical employment dataset bindings drifted")

    migrations = tuple(
        item
        for item in registry.migrations
        if item.id != _EMPLOYMENT_VINTAGE_MIGRATION_ID
    )
    collectors = tuple(
        item
        for item in registry.collectors
        if str(item["id"]) not in _EMPLOYMENT_VINTAGE_COLLECTOR_IDS
    )
    datasets = tuple(
        replace(
            item,
            collector_ids=tuple(
                collector_id
                for collector_id in item.collector_ids
                if collector_id not in _EMPLOYMENT_VINTAGE_COLLECTOR_IDS
            ),
        )
        if item.id in _MACRO_VINTAGE_DATASET_IDS
        else item
        for item in registry.datasets
    )
    stores = tuple(
        replace(
            store,
            migration_order=tuple(
                migration_id
                for migration_id in store.migration_order
                if migration_id != _EMPLOYMENT_VINTAGE_MIGRATION_ID
            ),
        )
        for store in registry.stores
    )
    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.16.0"
    raw["migrations"] = [
        item
        for item in raw["migrations"]
        if item["id"] != _EMPLOYMENT_VINTAGE_MIGRATION_ID
    ]
    raw["collectors"] = [
        item
        for item in raw["collectors"]
        if item["id"] not in _EMPLOYMENT_VINTAGE_COLLECTOR_IDS
    ]
    for dataset in raw["datasets"]:
        if dataset.get("id") in _MACRO_VINTAGE_DATASET_IDS:
            dataset["collector_ids"] = [
                collector_id
                for collector_id in dataset["collector_ids"]
                if collector_id not in _EMPLOYMENT_VINTAGE_COLLECTOR_IDS
            ]
    for store in raw["stores"]:
        store["migration_order"] = [
            migration_id
            for migration_id in store["migration_order"]
            if migration_id != _EMPLOYMENT_VINTAGE_MIGRATION_ID
        ]
    return replace(
        registry,
        registry_version="2.16.0",
        stores=stores,
        migrations=migrations,
        datasets=datasets,
        collectors=collectors,
        raw=raw,
        source_sha256=_PRE_EMPLOYMENT_VINTAGE_REGISTRY_SOURCE_SHA256,
    )


def macro_vintage_registry_profile(registry: Registry) -> Registry:
    """Project the live GDP/CPI vintage revision back to registry 2.15."""

    registry = market_return_v2_registry_profile(registry)

    version = (registry.schema_version, registry.registry_version)
    if version in {
        ("1.8.0", "2.31.0"),
        ("1.8.0", "2.30.0"),
        ("1.8.0", "2.29.0"),
        ("1.8.0", "2.28.0"),
        ("1.8.0", "2.27.0"),
        ("1.8.0", "2.26.0"),
        ("1.8.0", "2.25.0"),
        ("1.8.0", "2.24.0"),
        ("1.8.0", "2.17.0"),
        ("1.8.0", "2.18.0"),
        ("1.8.0", "2.19.0"),
        ("1.8.0", "2.20.0"),
        ("1.8.0", "2.21.0"),
        ("1.8.0", "2.23.0"),
    }:
        registry = employment_vintage_registry_profile(registry)
        version = (registry.schema_version, registry.registry_version)
    if version == ("1.8.0", "2.15.0"):
        if (
            registry.source_sha256 != _PRE_MACRO_VINTAGE_REGISTRY_SOURCE_SHA256
            or registry.raw.get("registry_version") != "2.15.0"
            or len(registry.migrations) != 34
            or len(registry.datasets) != 48
            or len(registry.collectors) != 28
        ):
            raise RegistryError("Historical pre-vintage registry profile drifted")
        return registry

    if (
        version != ("1.8.0", "2.16.0")
        or len(registry.migrations) != 35
        or len(registry.datasets) != 50
        or len(registry.collectors) != 30
        or registry.store("macro").migration_order[-1]
        != _MACRO_VINTAGE_MIGRATION_ID
        or _MACRO_VINTAGE_MIGRATION_ID
        not in {item.id for item in registry.migrations}
        or not _MACRO_VINTAGE_DATASET_IDS.issubset(
            {item.id for item in registry.datasets}
        )
        or not _MACRO_VINTAGE_COLLECTOR_IDS.issubset(
            {str(item["id"]) for item in registry.collectors}
        )
    ):
        raise RegistryError("Canonical registry cannot reproduce revision 2.15")

    migrations = tuple(
        item for item in registry.migrations if item.id != _MACRO_VINTAGE_MIGRATION_ID
    )
    datasets = tuple(
        item for item in registry.datasets if item.id not in _MACRO_VINTAGE_DATASET_IDS
    )
    collectors = tuple(
        item
        for item in registry.collectors
        if str(item["id"]) not in _MACRO_VINTAGE_COLLECTOR_IDS
    )
    stores = tuple(
        replace(
            store,
            migration_order=tuple(
                migration_id
                for migration_id in store.migration_order
                if migration_id != _MACRO_VINTAGE_MIGRATION_ID
            ),
        )
        for store in registry.stores
    )
    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.15.0"
    raw["migrations"] = [
        item
        for item in raw["migrations"]
        if item["id"] != _MACRO_VINTAGE_MIGRATION_ID
    ]
    raw["datasets"] = [
        item for item in raw["datasets"] if item["id"] not in _MACRO_VINTAGE_DATASET_IDS
    ]
    raw["collectors"] = [
        item
        for item in raw["collectors"]
        if item["id"] not in _MACRO_VINTAGE_COLLECTOR_IDS
    ]
    for store in raw["stores"]:
        store["migration_order"] = [
            migration_id
            for migration_id in store["migration_order"]
            if migration_id != _MACRO_VINTAGE_MIGRATION_ID
        ]
    return replace(
        registry,
        registry_version="2.15.0",
        stores=stores,
        migrations=migrations,
        datasets=datasets,
        collectors=collectors,
        raw=raw,
        source_sha256=_PRE_MACRO_VINTAGE_REGISTRY_SOURCE_SHA256,
    )


def stage12d_registry_profile(registry: Registry) -> Registry:
    """Project the current basename-aligned registry back to Stage 12D."""

    registry = market_return_v2_registry_profile(registry)

    historical_defaults = {
        "market": "data/market.sqlite",
        "macro": "data/macro_data.sqlite",
        "company": "data/company_data.sqlite",
        "news": "data/news_data.sqlite",
    }
    current_defaults = {
        "market": "data/market.sqlite",
        "macro": "data/macro.sqlite",
        "company": "data/company.sqlite",
        "news": "data/news.sqlite",
    }
    version = (registry.schema_version, registry.registry_version)
    if version in {
        ("1.8.0", "2.31.0"),
        ("1.8.0", "2.30.0"),
        ("1.8.0", "2.29.0"),
        ("1.8.0", "2.28.0"),
        ("1.8.0", "2.27.0"),
        ("1.8.0", "2.26.0"),
        ("1.8.0", "2.25.0"),
        ("1.8.0", "2.24.0"),
        ("1.8.0", "2.16.0"),
        ("1.8.0", "2.17.0"),
        ("1.8.0", "2.18.0"),
        ("1.8.0", "2.19.0"),
        ("1.8.0", "2.20.0"),
        ("1.8.0", "2.21.0"),
        ("1.8.0", "2.23.0"),
    }:
        registry = macro_vintage_registry_profile(registry)
        version = (registry.schema_version, registry.registry_version)
    if version == ("1.8.0", "2.14.0"):
        if (
            registry.source_sha256 != _STAGE12C_REGISTRY_SOURCE_SHA256
            or registry.raw.get("registry_version") != "2.14.0"
            or {
                role: registry.store(role).default_path
                for role in historical_defaults
            }
            != historical_defaults
        ):
            raise RegistryError("Historical Stage 12D registry profile drifted")
        return registry

    if (
        version != ("1.8.0", "2.15.0")
        or len(registry.migrations) != 34
        or len(registry.datasets) != 48
        or len(registry.collectors) != 28
        or {
            role: registry.store(role).default_path for role in current_defaults
        }
        != current_defaults
    ):
        raise RegistryError("Canonical registry cannot reproduce Stage 12D")

    stores = tuple(
        replace(store, default_path=historical_defaults[store.id])
        for store in registry.stores
    )
    raw = copy.deepcopy(dict(registry.raw))
    raw_stores = {item.get("id"): item for item in raw["stores"]}
    if set(raw_stores) != set(current_defaults) or any(
        raw_stores[role].get("default_path") != current_defaults[role]
        for role in current_defaults
    ):
        raise RegistryError("Canonical raw store defaults drifted")
    for role, default_path in historical_defaults.items():
        raw_stores[role]["default_path"] = default_path
    raw["registry_version"] = "2.14.0"
    return replace(
        registry,
        registry_version="2.14.0",
        stores=stores,
        raw=raw,
        source_sha256=_STAGE12C_REGISTRY_SOURCE_SHA256,
    )


def stage12c_registry_profile(registry: Registry) -> Registry:
    """Project the Stage 12C manual collector revision back to Stage 12B."""

    registry = market_return_v2_registry_profile(registry)

    version = (registry.schema_version, registry.registry_version)
    if version in {
        ("1.8.0", "2.31.0"),
        ("1.8.0", "2.30.0"),
        ("1.8.0", "2.29.0"),
        ("1.8.0", "2.28.0"),
        ("1.8.0", "2.27.0"),
        ("1.8.0", "2.26.0"),
        ("1.8.0", "2.25.0"),
        ("1.8.0", "2.24.0"),
        ("1.8.0", "2.15.0"),
        ("1.8.0", "2.16.0"),
        ("1.8.0", "2.17.0"),
        ("1.8.0", "2.18.0"),
        ("1.8.0", "2.19.0"),
        ("1.8.0", "2.20.0"),
        ("1.8.0", "2.21.0"),
        ("1.8.0", "2.23.0"),
    }:
        registry = stage12d_registry_profile(registry)
        version = (registry.schema_version, registry.registry_version)
    if version == ("1.8.0", "2.13.0"):
        if (
            registry.source_sha256 != _STAGE12B_REGISTRY_SOURCE_SHA256
            or registry.raw.get("registry_version") != "2.13.0"
            or len(registry.migrations) != 34
            or len(registry.datasets) != 48
            or len(registry.collectors) != 27
            or _STAGE12C_COLLECTOR_ID
            in {str(item["id"]) for item in registry.collectors}
            or any(
                _STAGE12C_COLLECTOR_ID in dataset.collector_ids
                for dataset in registry.datasets
            )
        ):
            raise RegistryError("Historical Stage 12B registry profile drifted")
        return registry

    if (
        version != ("1.8.0", "2.14.0")
        or registry.store("market").default_path != "data/market.sqlite"
        or len(registry.migrations) != 34
        or len(registry.datasets) != 48
        or len(registry.collectors) != 28
    ):
        raise RegistryError("Canonical registry cannot reproduce the Stage 12B profile")

    collector_matches = tuple(
        item
        for item in registry.collectors
        if str(item["id"]) == _STAGE12C_COLLECTOR_ID
    )
    datasets_by_id = {item.id: item for item in registry.datasets}
    if len(collector_matches) != 1 or not set(
        _STAGE12C_OUTPUT_DATASET_IDS
    ).issubset(datasets_by_id):
        raise RegistryError("Canonical Stage 12C collector is incomplete")
    collector = collector_matches[0]
    if (
        collector["version"] != "1.0.0"
        or collector["handler"] != "market.stage12c_fmp_daily_incremental_manual"
        or collector["network"] is not True
        or tuple(collector["configuration_env"]) != ("FMP_API_KEY",)
        or tuple(collector["input_datasets"]) != _STAGE12C_INPUT_DATASET_IDS
        or tuple(collector["output_datasets"]) != _STAGE12C_OUTPUT_DATASET_IDS
        or dict(collector["semantic_identity"])
        != {
            "excludes": [
                "api_key",
                "captured_at",
                "http_headers",
                "source_row_order",
            ],
            "includes": [
                "scope_manifest_sha256",
                "request_scope",
                "normalization_version",
                "normalized_complete_batch",
            ],
        }
        or dict(collector["mutation_policy"])
        != {
            "mode": "append_versions_and_move_current_projection",
            "unchanged": "zero_persistent_writes",
        }
        or dict(collector["workload_bounds"])
        != {
            "max_requests": 1,
            "max_rows": 2,
            "max_bytes": 65_536,
            "max_seconds": 45,
        }
        or dict(collector["retry_policy"])
        != {
            "transient_classes": [],
            "max_attempts": 1,
            "backoff": "none",
            "honor_retry_after": False,
        }
        or collector["physical_locks"] != "derived_from_output_store_paths"
        or dict(collector["schedule_eligibility"]) != {"mode": "manual_only"}
        or any(
            tuple(datasets_by_id[dataset_id].collector_ids)
            != expected_collector_ids
            for dataset_id, expected_collector_ids in (
                _STAGE12C_EXPECTED_DATASET_COLLECTOR_IDS.items()
            )
        )
    ):
        raise RegistryError("Canonical Stage 12C collector drifted")

    raw = copy.deepcopy(dict(registry.raw))
    raw_collector_matches = [
        item
        for item in raw["collectors"]
        if item.get("id") == _STAGE12C_COLLECTOR_ID
    ]
    if len(raw_collector_matches) != 1:
        raise RegistryError("Canonical Stage 12C raw collector is incomplete")
    raw["collectors"] = [
        item
        for item in raw["collectors"]
        if item["id"] != _STAGE12C_COLLECTOR_ID
    ]
    for dataset in raw["datasets"]:
        expected_collector_ids = _STAGE12C_EXPECTED_DATASET_COLLECTOR_IDS.get(
            dataset.get("id")
        )
        if expected_collector_ids is None:
            continue
        if tuple(dataset.get("collector_ids", ())) != expected_collector_ids:
            raise RegistryError("Canonical Stage 12C dataset binding drifted")
        dataset["collector_ids"] = [
            collector_id
            for collector_id in dataset["collector_ids"]
            if collector_id != _STAGE12C_COLLECTOR_ID
        ]
    raw["registry_version"] = "2.13.0"
    datasets = tuple(
        replace(
            dataset,
            collector_ids=tuple(
                collector_id
                for collector_id in dataset.collector_ids
                if collector_id != _STAGE12C_COLLECTOR_ID
            ),
        )
        for dataset in registry.datasets
    )
    collectors = tuple(
        item
        for item in registry.collectors
        if str(item["id"]) != _STAGE12C_COLLECTOR_ID
    )
    return replace(
        registry,
        registry_version="2.13.0",
        datasets=datasets,
        collectors=collectors,
        raw=raw,
        source_sha256=_STAGE12B_REGISTRY_SOURCE_SHA256,
    )


def stage12b_registry_profile(registry: Registry) -> Registry:
    """Project the Stage 12B fixture collector revision back to Stage 12A."""

    registry = market_return_v2_registry_profile(registry)

    version = (registry.schema_version, registry.registry_version)
    if version in {
        ("1.8.0", "2.31.0"),
        ("1.8.0", "2.30.0"),
        ("1.8.0", "2.29.0"),
        ("1.8.0", "2.28.0"),
        ("1.8.0", "2.27.0"),
        ("1.8.0", "2.26.0"),
        ("1.8.0", "2.25.0"),
        ("1.8.0", "2.24.0"),
        ("1.8.0", "2.17.0"),
        ("1.8.0", "2.16.0"),
        ("1.8.0", "2.15.0"),
        ("1.8.0", "2.14.0"),
        ("1.8.0", "2.18.0"),
        ("1.8.0", "2.19.0"),
        ("1.8.0", "2.20.0"),
        ("1.8.0", "2.21.0"),
        ("1.8.0", "2.23.0"),
    }:
        registry = stage12c_registry_profile(registry)
        version = (registry.schema_version, registry.registry_version)
    if version == ("1.8.0", "2.12.0"):
        raw_market = [
            store for store in registry.raw["stores"] if store.get("id") == "market"
        ]
        if (
            registry.source_sha256 != _STAGE12A_REGISTRY_SOURCE_SHA256
            or registry.store("market").default_path != "data/market.sqlite"
            or registry.raw.get("registry_version") != "2.12.0"
            or len(raw_market) != 1
            or raw_market[0].get("default_path") != "data/market.sqlite"
            or _STAGE12B_COLLECTOR_ID
            in {str(item["id"]) for item in registry.collectors}
            or any(
                _STAGE12B_COLLECTOR_ID in dataset.collector_ids
                for dataset in registry.datasets
            )
        ):
            raise RegistryError("Historical Stage 12A registry profile drifted")
        return registry

    if (
        version != ("1.8.0", "2.13.0")
        or registry.store("market").default_path != "data/market.sqlite"
        or len(registry.migrations) != 34
        or len(registry.datasets) != 48
        or len(registry.collectors) != 27
    ):
        raise RegistryError("Canonical registry cannot reproduce the Stage 12A profile")

    collector_matches = tuple(
        item
        for item in registry.collectors
        if str(item["id"]) == _STAGE12B_COLLECTOR_ID
    )
    datasets_by_id = {item.id: item for item in registry.datasets}
    if len(collector_matches) != 1 or not set(
        _STAGE12B_OUTPUT_DATASET_IDS
    ).issubset(datasets_by_id):
        raise RegistryError("Canonical Stage 12B fixture collector is incomplete")
    collector = collector_matches[0]
    if (
        collector["version"] != "1.0.0"
        or collector["handler"] != "market.stage12b_fmp_daily_incremental_fixture"
        or collector["network"] is not False
        or tuple(collector["input_datasets"]) != _STAGE12B_INPUT_DATASET_IDS
        or tuple(collector["output_datasets"]) != _STAGE12B_OUTPUT_DATASET_IDS
        or dict(collector["semantic_identity"])
        != {
            "includes": [
                "request_scope",
                "normalization_version",
                "normalized_complete_batch",
            ],
            "excludes": ["captured_at", "source_row_order"],
        }
        or dict(collector["mutation_policy"])
        != {
            "mode": "append_versions_and_move_current_projection",
            "unchanged": "zero_persistent_writes",
        }
        or dict(collector["workload_bounds"])
        != {
            "max_requests": 1,
            "max_rows": 5,
            "max_bytes": 65_536,
            "max_seconds": 30,
        }
        or dict(collector["retry_policy"])
        != {
            "transient_classes": [],
            "max_attempts": 1,
            "backoff": "none",
            "honor_retry_after": False,
        }
        or tuple(collector["configuration_env"])
        or collector["physical_locks"] != "derived_from_output_store_paths"
        or dict(collector["schedule_eligibility"]) != {"mode": "manual_only"}
        or any(
            tuple(datasets_by_id[dataset_id].collector_ids)
            != expected_collector_ids
            for dataset_id, expected_collector_ids in (
                _STAGE12B_EXPECTED_DATASET_COLLECTOR_IDS.items()
            )
        )
    ):
        raise RegistryError("Canonical Stage 12B fixture collector drifted")

    raw = copy.deepcopy(dict(registry.raw))
    raw_collector_matches = [
        item
        for item in raw["collectors"]
        if item.get("id") == _STAGE12B_COLLECTOR_ID
    ]
    if len(raw_collector_matches) != 1:
        raise RegistryError("Canonical Stage 12B raw collector is incomplete")
    raw["collectors"] = [
        item
        for item in raw["collectors"]
        if item["id"] != _STAGE12B_COLLECTOR_ID
    ]
    for dataset in raw["datasets"]:
        expected_collector_ids = _STAGE12B_EXPECTED_DATASET_COLLECTOR_IDS.get(
            dataset.get("id")
        )
        if expected_collector_ids is None:
            continue
        if tuple(dataset.get("collector_ids", ())) != expected_collector_ids:
            raise RegistryError("Canonical Stage 12B dataset binding drifted")
        dataset["collector_ids"] = [
            collector_id
            for collector_id in dataset["collector_ids"]
            if collector_id != _STAGE12B_COLLECTOR_ID
        ]
    raw["registry_version"] = "2.12.0"
    datasets = tuple(
        replace(
            dataset,
            collector_ids=tuple(
                collector_id
                for collector_id in dataset.collector_ids
                if collector_id != _STAGE12B_COLLECTOR_ID
            ),
        )
        for dataset in registry.datasets
    )
    collectors = tuple(
        item
        for item in registry.collectors
        if str(item["id"]) != _STAGE12B_COLLECTOR_ID
    )
    return replace(
        registry,
        registry_version="2.12.0",
        datasets=datasets,
        collectors=collectors,
        raw=raw,
        source_sha256=_STAGE12A_REGISTRY_SOURCE_SHA256,
    )


def stage12_registry_profile(registry: Registry) -> Registry:
    """Project the path-only Stage 12 revision back to canonical revision 2.11."""

    registry = market_return_v2_registry_profile(registry)

    version = (registry.schema_version, registry.registry_version)
    if version in {
        ("1.8.0", "2.31.0"),
        ("1.8.0", "2.30.0"),
        ("1.8.0", "2.29.0"),
        ("1.8.0", "2.28.0"),
        ("1.8.0", "2.27.0"),
        ("1.8.0", "2.26.0"),
        ("1.8.0", "2.25.0"),
        ("1.8.0", "2.24.0"),
        ("1.8.0", "2.17.0"),
        ("1.8.0", "2.16.0"),
        ("1.8.0", "2.15.0"),
        ("1.8.0", "2.14.0"),
        ("1.8.0", "2.18.0"),
        ("1.8.0", "2.19.0"),
        ("1.8.0", "2.20.0"),
        ("1.8.0", "2.21.0"),
        ("1.8.0", "2.23.0"),
    }:
        registry = stage12c_registry_profile(registry)
        version = (registry.schema_version, registry.registry_version)
    if version == ("1.8.0", "2.13.0"):
        registry = stage12b_registry_profile(registry)
        version = (registry.schema_version, registry.registry_version)
    if version == ("1.8.0", "2.11.0"):
        if (
            registry.store("market").default_path != "data/market_data.sqlite"
            or registry.source_sha256 != _STAGE12_REGISTRY_SOURCE_SHA256
        ):
            raise RegistryError("Historical Stage 12 registry profile drifted")
        return registry
    if (
        version != ("1.8.0", "2.12.0")
        or registry.store("market").default_path != "data/market.sqlite"
    ):
        raise RegistryError("Canonical registry cannot reproduce the pre-Stage 12 profile")

    stores = tuple(
        replace(store, default_path="data/market_data.sqlite")
        if store.id == "market"
        else store
        for store in registry.stores
    )
    raw = copy.deepcopy(dict(registry.raw))
    raw_market = [
        store for store in raw["stores"] if store.get("id") == "market"
    ]
    if (
        len(raw_market) != 1
        or raw_market[0].get("default_path") != "data/market.sqlite"
    ):
        raise RegistryError("Canonical Stage 12 market path drifted")
    raw_market[0]["default_path"] = "data/market_data.sqlite"
    raw["registry_version"] = "2.11.0"
    return replace(
        registry,
        registry_version="2.11.0",
        stores=stores,
        raw=raw,
        source_sha256=_STAGE12_REGISTRY_SOURCE_SHA256,
    )


def stage11_registry_profile(registry: Registry) -> Registry:
    """Project the current Stage 12 revision back to Stage 11 exactly."""

    registry = market_return_v2_registry_profile(registry)

    if (
        registry.schema_version == "1.8.0"
        and registry.registry_version
        in {
            "2.31.0",
            "2.30.0",
            "2.29.0",
            "2.28.0",
            "2.27.0",
            "2.26.0",
            "2.25.0",
            "2.24.0",
            "2.12.0",
            "2.13.0",
            "2.14.0",
            "2.15.0",
            "2.16.0",
            "2.17.0",
            "2.18.0",
            "2.19.0",
            "2.20.0",
            "2.21.0",
            "2.23.0",
        }
    ):
        registry = stage12_registry_profile(registry)

    stage11_collectors = tuple(
        item
        for item in registry.collectors
        if str(item["id"]) in _STAGE11_COLLECTOR_IDS
    )
    if (
        registry.schema_version == "1.7.0"
        and registry.registry_version == "2.9.0"
    ):
        if (
            registry.source_sha256 != _STAGE11_REGISTRY_SOURCE_SHA256
            or len(stage11_collectors) != 3
            or any(
                dict(item["retry_policy"]) != _STAGE11_HISTORICAL_RETRY_POLICY
                for item in stage11_collectors
            )
        ):
            raise RegistryError("Historical Stage 11 registry profile drifted")
        return registry

    if registry.schema_version == "1.8.0" and registry.registry_version == "2.11.0":
        fmp_migrations = tuple(
            item
            for item in registry.migrations
            if item.id == _FMP_STOCK_LATEST_MIGRATION_ID
        )
        if (
            len(registry.migrations) != 34
            or len(registry.datasets) != 48
            or len(registry.collectors) != 26
            or len(stage11_collectors) != 3
            or len(fmp_migrations) != 1
            or fmp_migrations[0].store != "news"
            or fmp_migrations[0].ordinal != 5
            or not _FMP_STOCK_LATEST_DATASET_IDS.issubset(
                {item.id for item in registry.datasets}
            )
            or _FMP_STOCK_LATEST_COLLECTOR_ID
            not in {str(item["id"]) for item in registry.collectors}
            or registry.store("news").migration_order[-1]
            != _FMP_STOCK_LATEST_MIGRATION_ID
            or any(
                dict(item["retry_policy"]) != _STAGE11_CANONICAL_RETRY_POLICY
                for item in stage11_collectors
            )
        ):
            raise RegistryError(
                "Canonical registry cannot reproduce the Stage 11 profile"
            )
        migrations = tuple(
            item
            for item in registry.migrations
            if item.id != _FMP_STOCK_LATEST_MIGRATION_ID
        )
        datasets = tuple(
            item
            for item in registry.datasets
            if item.id not in _FMP_STOCK_LATEST_DATASET_IDS
        )
        collectors = tuple(
            item
            for item in registry.collectors
            if str(item["id"]) != _FMP_STOCK_LATEST_COLLECTOR_ID
        )
        stores = tuple(
            replace(
                store,
                migration_order=tuple(
                    migration_id
                    for migration_id in store.migration_order
                    if migration_id != _FMP_STOCK_LATEST_MIGRATION_ID
                ),
            )
            for store in registry.stores
        )
        raw = copy.deepcopy(dict(registry.raw))
        raw["schema_version"] = "1.7.0"
        raw["registry_version"] = "2.10.0"
        raw["migrations"] = [
            item
            for item in raw["migrations"]
            if item["id"] != _FMP_STOCK_LATEST_MIGRATION_ID
        ]
        raw["datasets"] = [
            item
            for item in raw["datasets"]
            if item["id"] not in _FMP_STOCK_LATEST_DATASET_IDS
        ]
        raw["collectors"] = [
            item
            for item in raw["collectors"]
            if item["id"] != _FMP_STOCK_LATEST_COLLECTOR_ID
        ]
        for store in raw["stores"]:
            store["migration_order"] = [
                migration_id
                for migration_id in store["migration_order"]
                if migration_id != _FMP_STOCK_LATEST_MIGRATION_ID
            ]
        registry = replace(
            registry,
            schema_version="1.7.0",
            registry_version="2.10.0",
            stores=stores,
            migrations=migrations,
            datasets=datasets,
            collectors=collectors,
            raw=raw,
        )
        stage11_collectors = tuple(
            item
            for item in registry.collectors
            if str(item["id"]) in _STAGE11_COLLECTOR_IDS
        )

    if (
        registry.schema_version != "1.7.0"
        or registry.registry_version != "2.10.0"
        or len(registry.migrations) != 33
        or len(registry.datasets) != 46
        or len(registry.collectors) != 25
        or len(stage11_collectors) != 3
        or any(
            dict(item["retry_policy"]) != _STAGE11_CANONICAL_RETRY_POLICY
            for item in stage11_collectors
        )
    ):
        raise RegistryError("Canonical registry cannot reproduce the Stage 11 profile")

    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.9.0"
    for collector in raw["collectors"]:
        if collector["id"] in _STAGE11_COLLECTOR_IDS:
            collector["retry_policy"] = copy.deepcopy(
                _STAGE11_HISTORICAL_RETRY_POLICY
            )
    return replace(
        registry,
        registry_version="2.9.0",
        collectors=tuple(raw["collectors"]),
        raw=raw,
        source_sha256=_STAGE11_REGISTRY_SOURCE_SHA256,
    )


def stage10_registry_profile(registry: Registry) -> Registry:
    """Project the canonical Stage 11 registry back to Stage 10 exactly."""

    registry = market_return_v2_registry_profile(registry)

    if (
        (registry.schema_version, registry.registry_version)
        in {
            ("1.8.0", "2.31.0"),
            ("1.8.0", "2.30.0"),
            ("1.8.0", "2.29.0"),
            ("1.8.0", "2.28.0"),
            ("1.8.0", "2.27.0"),
            ("1.8.0", "2.26.0"),
            ("1.8.0", "2.25.0"),
            ("1.8.0", "2.24.0"),
            ("1.8.0", "2.14.0"),
            ("1.8.0", "2.15.0"),
            ("1.8.0", "2.16.0"),
            ("1.8.0", "2.17.0"),
            ("1.8.0", "2.18.0"),
            ("1.8.0", "2.19.0"),
            ("1.8.0", "2.20.0"),
            ("1.8.0", "2.21.0"),
            ("1.8.0", "2.23.0"),
            ("1.8.0", "2.13.0"),
            ("1.8.0", "2.12.0"),
            ("1.8.0", "2.11.0"),
            ("1.7.0", "2.9.0"),
            ("1.7.0", "2.10.0"),
        }
    ):
        registry = stage11_registry_profile(registry)
    if (
        registry.schema_version != "1.7.0"
        or registry.registry_version != "2.9.0"
        or len(registry.migrations) != 33
        or len(registry.datasets) != 46
        or len(registry.collectors) != 25
        or tuple(str(item["id"]) for item in registry.tools) != PUBLIC_TOOL_NAMES
        or tuple(item.id for item in registry.jobs) != _STAGE7_JOB_IDS
        or tuple(item.id for item in registry.exports) != (_STAGE8_EXPORT_ID,)
        or _STAGE11_MIGRATION_ID not in {item.id for item in registry.migrations}
        or not _STAGE11_DATASET_IDS.issubset(
            {item.id for item in registry.datasets}
        )
        or not _STAGE11_COLLECTOR_IDS.issubset(
            {str(item["id"]) for item in registry.collectors}
        )
    ):
        raise RegistryError("Canonical registry cannot reproduce the Stage 10 profile")

    migrations = tuple(
        item for item in registry.migrations if item.id != _STAGE11_MIGRATION_ID
    )
    datasets = tuple(
        item for item in registry.datasets if item.id not in _STAGE11_DATASET_IDS
    )
    collectors = tuple(
        item
        for item in registry.collectors
        if str(item["id"]) not in _STAGE11_COLLECTOR_IDS
    )
    stores = tuple(
        replace(
            store,
            migration_order=tuple(
                migration_id
                for migration_id in store.migration_order
                if migration_id != _STAGE11_MIGRATION_ID
            ),
        )
        for store in registry.stores
    )
    raw = copy.deepcopy(dict(registry.raw))
    raw["schema_version"] = "1.6.0"
    raw["registry_version"] = "2.8.0"
    raw["migrations"] = [
        item for item in raw["migrations"] if item["id"] != _STAGE11_MIGRATION_ID
    ]
    raw["datasets"] = [
        item for item in raw["datasets"] if item["id"] not in _STAGE11_DATASET_IDS
    ]
    raw["collectors"] = [
        item for item in raw["collectors"] if item["id"] not in _STAGE11_COLLECTOR_IDS
    ]
    for store in raw["stores"]:
        store["migration_order"] = [
            migration_id
            for migration_id in store["migration_order"]
            if migration_id != _STAGE11_MIGRATION_ID
        ]
    return replace(
        registry,
        schema_version="1.6.0",
        registry_version="2.8.0",
        stores=stores,
        migrations=migrations,
        datasets=datasets,
        collectors=collectors,
        raw=raw,
        source_sha256=_STAGE10_REGISTRY_SOURCE_SHA256,
    )


def _stage10_input(registry: Registry) -> Registry:
    registry = market_return_v2_registry_profile(registry)
    if (
        (registry.schema_version, registry.registry_version)
        in {
            ("1.8.0", "2.31.0"),
            ("1.8.0", "2.30.0"),
            ("1.8.0", "2.29.0"),
            ("1.8.0", "2.28.0"),
            ("1.8.0", "2.27.0"),
            ("1.8.0", "2.26.0"),
            ("1.8.0", "2.25.0"),
            ("1.8.0", "2.24.0"),
            ("1.8.0", "2.14.0"),
            ("1.8.0", "2.15.0"),
            ("1.8.0", "2.16.0"),
            ("1.8.0", "2.17.0"),
            ("1.8.0", "2.18.0"),
            ("1.8.0", "2.19.0"),
            ("1.8.0", "2.20.0"),
            ("1.8.0", "2.21.0"),
            ("1.8.0", "2.23.0"),
            ("1.8.0", "2.13.0"),
            ("1.8.0", "2.12.0"),
            ("1.8.0", "2.11.0"),
            ("1.7.0", "2.9.0"),
            ("1.7.0", "2.10.0"),
        }
    ):
        return stage10_registry_profile(registry)
    return registry


def stage9_registry_profile(registry: Registry) -> Registry:
    """Project the canonical registry back to Stage 9 exactly."""

    registry = _stage10_input(registry)

    if (
        registry.schema_version != "1.6.0"
        or registry.registry_version != "2.8.0"
        or len(registry.migrations) != 32
        or len(registry.datasets) != 40
        or len(registry.collectors) != 22
        or tuple(str(item["id"]) for item in registry.tools) != PUBLIC_TOOL_NAMES
        or tuple(item.id for item in registry.jobs) != _STAGE7_JOB_IDS
        or tuple(item.id for item in registry.exports) != (_STAGE8_EXPORT_ID,)
        or _STAGE10_MIGRATION_ID
        not in {item.id for item in registry.migrations}
        or not _STAGE10_DATASET_IDS.issubset(
            {item.id for item in registry.datasets}
        )
        or not _STAGE10_COLLECTOR_IDS.issubset(
            {str(item["id"]) for item in registry.collectors}
        )
    ):
        raise RegistryError("Canonical registry cannot reproduce the Stage 9 profile")

    migrations = tuple(
        item for item in registry.migrations if item.id != _STAGE10_MIGRATION_ID
    )
    datasets = tuple(
        item for item in registry.datasets if item.id not in _STAGE10_DATASET_IDS
    )
    collectors = tuple(
        item
        for item in registry.collectors
        if str(item["id"]) not in _STAGE10_COLLECTOR_IDS
    )
    stores = tuple(
        replace(
            store,
            migration_order=tuple(
                migration_id
                for migration_id in store.migration_order
                if migration_id != _STAGE10_MIGRATION_ID
            ),
        )
        for store in registry.stores
    )

    raw = copy.deepcopy(dict(registry.raw))
    raw["schema_version"] = "1.5.0"
    raw["registry_version"] = "2.7.0"
    raw["migrations"] = [
        item
        for item in raw["migrations"]
        if item["id"] != _STAGE10_MIGRATION_ID
    ]
    raw["datasets"] = [
        item for item in raw["datasets"] if item["id"] not in _STAGE10_DATASET_IDS
    ]
    raw["collectors"] = [
        item
        for item in raw["collectors"]
        if item["id"] not in _STAGE10_COLLECTOR_IDS
    ]
    for store in raw["stores"]:
        store["migration_order"] = [
            migration_id
            for migration_id in store["migration_order"]
            if migration_id != _STAGE10_MIGRATION_ID
        ]
    return replace(
        registry,
        schema_version="1.5.0",
        registry_version="2.7.0",
        stores=stores,
        migrations=migrations,
        datasets=datasets,
        collectors=collectors,
        raw=raw,
        source_sha256=_STAGE9_REGISTRY_SOURCE_SHA256,
    )


def _stage9_input(registry: Registry) -> Registry:
    registry = _stage10_input(registry)
    if registry.schema_version == "1.6.0" and registry.registry_version == "2.8.0":
        return stage9_registry_profile(registry)
    return registry


def stage8_registry_profile(registry: Registry) -> Registry:
    """Project the canonical Stage 9 registry back to Stage 8 exactly."""

    registry = _stage9_input(registry)

    if (
        registry.schema_version != "1.5.0"
        or registry.registry_version != "2.7.0"
        or len(registry.migrations) != 31
        or len(registry.datasets) != 36
        or len(registry.collectors) != 20
        or tuple(str(item["id"]) for item in registry.tools) != PUBLIC_TOOL_NAMES
        or tuple(item.id for item in registry.jobs) != _STAGE7_JOB_IDS
        or tuple(item.id for item in registry.exports) != (_STAGE8_EXPORT_ID,)
        or _STAGE9_MIGRATION_ID not in {item.id for item in registry.migrations}
        or not _STAGE9_DATASET_IDS.issubset(
            {item.id for item in registry.datasets}
        )
        or _STAGE9_COLLECTOR_ID
        not in {str(item["id"]) for item in registry.collectors}
    ):
        raise RegistryError("Canonical registry cannot reproduce the Stage 8 profile")

    migrations = tuple(
        item for item in registry.migrations if item.id != _STAGE9_MIGRATION_ID
    )
    datasets = tuple(
        item for item in registry.datasets if item.id not in _STAGE9_DATASET_IDS
    )
    collectors = tuple(
        item
        for item in registry.collectors
        if str(item["id"]) != _STAGE9_COLLECTOR_ID
    )
    stores = tuple(
        replace(
            store,
            migration_order=tuple(
                migration_id
                for migration_id in store.migration_order
                if migration_id != _STAGE9_MIGRATION_ID
            ),
        )
        for store in registry.stores
    )

    raw = copy.deepcopy(dict(registry.raw))
    raw["schema_version"] = "1.4.0"
    raw["registry_version"] = "2.6.0"
    raw["migrations"] = [
        item for item in raw["migrations"] if item["id"] != _STAGE9_MIGRATION_ID
    ]
    raw["datasets"] = [
        item for item in raw["datasets"] if item["id"] not in _STAGE9_DATASET_IDS
    ]
    raw["collectors"] = [
        item for item in raw["collectors"] if item["id"] != _STAGE9_COLLECTOR_ID
    ]
    for store in raw["stores"]:
        store["migration_order"] = [
            migration_id
            for migration_id in store["migration_order"]
            if migration_id != _STAGE9_MIGRATION_ID
        ]
    return replace(
        registry,
        schema_version="1.4.0",
        registry_version="2.6.0",
        stores=stores,
        migrations=migrations,
        datasets=datasets,
        collectors=collectors,
        raw=raw,
        source_sha256=_STAGE8_REGISTRY_SOURCE_SHA256,
    )


def _stage8_input(registry: Registry) -> Registry:
    registry = _stage9_input(registry)
    if registry.schema_version == "1.5.0" and registry.registry_version == "2.7.0":
        return stage8_registry_profile(registry)
    return registry


def stage2_registry_profile(registry: Registry) -> Registry:
    """Project the validated additive registry back to the Stage 2 contract.

    Stage 1 and Stage 2 evidence is immutable historical evidence.  Later
    additive registry revisions may append migrations, datasets, and offline
    collectors, but must not silently rewrite those earlier acceptance
    receipts.  This allow-listed projection is derived only from an already
    validated canonical registry and retains the original resource bytes.
    """

    payload = (
        json.dumps(
            registry.raw,
            ensure_ascii=True,
            indent=1 if (registry.schema_version, registry.registry_version) in {("1.9.0", "2.72.0"), ("1.9.0", "2.73.0"), ("1.9.0", "2.74.0"), ("1.9.0", "2.75.0"), ("1.9.0", "2.76.0"), ("1.9.0", "2.77.0"), ("1.9.0", "2.78.0"), ("1.9.0", "2.79.0"), ("1.9.0", "2.80.0"), ("1.9.0", "2.81.0"), ("1.9.0", "2.82.0"), ("1.9.0", "2.83.0"), ("1.9.0", "2.84.0"), ("1.9.0", "2.85.0"), ("1.9.0", "2.86.0"), ("1.9.0", "2.87.0"), ("1.9.0", "2.88.0"), ("1.9.0", "2.89.0"), ("1.9.0", "2.90.0"), ("1.9.0", "2.91.0"), ("1.9.0", "2.92.0"), ("1.9.0", "2.93.0"), ("1.9.0", "2.94.0")} else 2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    if hashlib.sha256(payload).hexdigest() == registry.source_sha256:
        registry = _stage8_input(registry)
    migration_ids = {item.id for item in registry.migrations}
    dataset_ids = {item.id for item in registry.datasets}
    collector_ids = {str(item["id"]) for item in registry.collectors}
    if (
        not _STAGE2_MIGRATION_IDS.issubset(migration_ids)
        or not _STAGE2_DATASET_IDS.issubset(dataset_ids)
        or not _STAGE2_COLLECTOR_IDS.issubset(collector_ids)
        or registry.registry_version.split(".", 1)[0] != "2"
    ):
        raise RegistryError("Canonical registry cannot reproduce the Stage 2 profile")

    migrations = tuple(
        item for item in registry.migrations if item.id in _STAGE2_MIGRATION_IDS
    )
    collectors = tuple(
        item
        for item in registry.collectors
        if str(item["id"]) in _STAGE2_COLLECTOR_IDS
    )
    datasets = tuple(
        replace(
            item,
            collector_ids=tuple(
                collector_id
                for collector_id in item.collector_ids
                if collector_id in _STAGE2_COLLECTOR_IDS
            ),
        )
        for item in registry.datasets
        if item.id in _STAGE2_DATASET_IDS
    )
    stores = tuple(
        replace(
            store,
            migration_order=tuple(
                migration_id
                for migration_id in store.migration_order
                if migration_id in _STAGE2_MIGRATION_IDS
            ),
        )
        for store in registry.stores
    )

    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.0.0"
    raw["jobs"] = []
    raw["migrations"] = [
        item
        for item in raw["migrations"]
        if item["id"] in _STAGE2_MIGRATION_IDS
    ]
    raw["datasets"] = [
        {
            **item,
            "collector_ids": [
                collector_id
                for collector_id in item["collector_ids"]
                if collector_id in _STAGE2_COLLECTOR_IDS
            ],
        }
        for item in raw["datasets"]
        if item["id"] in _STAGE2_DATASET_IDS
    ]
    raw["collectors"] = [
        item
        for item in raw["collectors"]
        if item["id"] in _STAGE2_COLLECTOR_IDS
    ]
    for store in raw["stores"]:
        store["migration_order"] = [
            migration_id
            for migration_id in store["migration_order"]
            if migration_id in _STAGE2_MIGRATION_IDS
        ]

    datasets, raw = _export_free_dataset_projection(datasets, raw)
    datasets, tools, dashboard, raw = _legacy_tool_projection(
        registry, datasets, raw
    )
    return replace(
        registry,
        schema_version="1.0.0",
        registry_version="2.0.0",
        stores=stores,
        migrations=migrations,
        datasets=datasets,
        collectors=collectors,
        jobs=(),
        exports=(),
        tools=tools,
        dashboard=dashboard,
        raw=raw,
    )


def stage3_registry_profile(registry: Registry) -> Registry:
    """Project an additive canonical registry back to the Stage 3 contract."""

    registry = _stage8_input(registry)
    migration_ids = {item.id for item in registry.migrations}
    dataset_ids = {item.id for item in registry.datasets}
    collector_ids = {str(item["id"]) for item in registry.collectors}
    if (
        not _STAGE3_MIGRATION_IDS.issubset(migration_ids)
        or not _STAGE3_DATASET_IDS.issubset(dataset_ids)
        or not _STAGE3_COLLECTOR_IDS.issubset(collector_ids)
        or registry.registry_version.split(".", 1)[0] != "2"
    ):
        raise RegistryError("Canonical registry cannot reproduce the Stage 3 profile")

    migrations = tuple(
        item for item in registry.migrations if item.id in _STAGE3_MIGRATION_IDS
    )
    collectors = tuple(
        item
        for item in registry.collectors
        if str(item["id"]) in _STAGE3_COLLECTOR_IDS
    )
    datasets = tuple(
        replace(
            item,
            collector_ids=tuple(
                collector_id
                for collector_id in item.collector_ids
                if collector_id in _STAGE3_COLLECTOR_IDS
            ),
        )
        for item in registry.datasets
        if item.id in _STAGE3_DATASET_IDS
    )
    stores = tuple(
        replace(
            store,
            migration_order=tuple(
                migration_id
                for migration_id in store.migration_order
                if migration_id in _STAGE3_MIGRATION_IDS
            ),
        )
        for store in registry.stores
    )

    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.1.0"
    raw["jobs"] = []
    raw["migrations"] = [
        item for item in raw["migrations"] if item["id"] in _STAGE3_MIGRATION_IDS
    ]
    raw["datasets"] = [
        {
            **item,
            "collector_ids": [
                collector_id
                for collector_id in item["collector_ids"]
                if collector_id in _STAGE3_COLLECTOR_IDS
            ],
        }
        for item in raw["datasets"]
        if item["id"] in _STAGE3_DATASET_IDS
    ]
    raw["collectors"] = [
        item for item in raw["collectors"] if item["id"] in _STAGE3_COLLECTOR_IDS
    ]
    for store in raw["stores"]:
        store["migration_order"] = [
            migration_id
            for migration_id in store["migration_order"]
            if migration_id in _STAGE3_MIGRATION_IDS
        ]

    datasets, raw = _export_free_dataset_projection(datasets, raw)
    datasets, tools, dashboard, raw = _legacy_tool_projection(
        registry, datasets, raw
    )
    return replace(
        registry,
        schema_version="1.0.0",
        registry_version="2.1.0",
        stores=stores,
        migrations=migrations,
        datasets=datasets,
        collectors=collectors,
        jobs=(),
        exports=(),
        tools=tools,
        dashboard=dashboard,
        raw=raw,
    )


def stage4_registry_profile(registry: Registry) -> Registry:
    """Project an additive canonical registry back to the Stage 4 contract."""

    registry = _stage8_input(registry)
    migration_ids = {item.id for item in registry.migrations}
    dataset_ids = {item.id for item in registry.datasets}
    collector_ids = {str(item["id"]) for item in registry.collectors}
    if (
        not _STAGE4_MIGRATION_IDS.issubset(migration_ids)
        or not _STAGE4_DATASET_IDS.issubset(dataset_ids)
        or not _STAGE4_COLLECTOR_IDS.issubset(collector_ids)
        or registry.registry_version.split(".", 1)[0] != "2"
    ):
        raise RegistryError("Canonical registry cannot reproduce the Stage 4 profile")

    migrations = tuple(
        item for item in registry.migrations if item.id in _STAGE4_MIGRATION_IDS
    )
    collectors = tuple(
        item
        for item in registry.collectors
        if str(item["id"]) in _STAGE4_COLLECTOR_IDS
    )
    datasets = tuple(
        replace(
            item,
            collector_ids=tuple(
                collector_id
                for collector_id in item.collector_ids
                if collector_id in _STAGE4_COLLECTOR_IDS
            ),
        )
        for item in registry.datasets
        if item.id in _STAGE4_DATASET_IDS
    )
    stores = tuple(
        replace(
            store,
            migration_order=tuple(
                migration_id
                for migration_id in store.migration_order
                if migration_id in _STAGE4_MIGRATION_IDS
            ),
        )
        for store in registry.stores
    )

    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.2.0"
    raw["jobs"] = []
    raw["migrations"] = [
        item for item in raw["migrations"] if item["id"] in _STAGE4_MIGRATION_IDS
    ]
    raw["datasets"] = [
        {
            **item,
            "collector_ids": [
                collector_id
                for collector_id in item["collector_ids"]
                if collector_id in _STAGE4_COLLECTOR_IDS
            ],
        }
        for item in raw["datasets"]
        if item["id"] in _STAGE4_DATASET_IDS
    ]
    raw["collectors"] = [
        item for item in raw["collectors"] if item["id"] in _STAGE4_COLLECTOR_IDS
    ]
    for store in raw["stores"]:
        store["migration_order"] = [
            migration_id
            for migration_id in store["migration_order"]
            if migration_id in _STAGE4_MIGRATION_IDS
        ]

    datasets, raw = _export_free_dataset_projection(datasets, raw)
    datasets, tools, dashboard, raw = _legacy_tool_projection(
        registry, datasets, raw
    )
    return replace(
        registry,
        schema_version="1.0.0",
        registry_version="2.2.0",
        stores=stores,
        migrations=migrations,
        datasets=datasets,
        collectors=collectors,
        jobs=(),
        exports=(),
        tools=tools,
        dashboard=dashboard,
        raw=raw,
    )


def stage5_registry_profile(registry: Registry) -> Registry:
    """Project the additive canonical registry back to Stage 5 exactly."""

    registry = _stage8_input(registry)
    if (
        registry.registry_version.split(".", 1)[0] != "2"
        or len(registry.migrations) != 30
        or len(registry.datasets) != 33
        or len(registry.collectors) != 19
        or tuple(str(item["id"]) for item in registry.tools) != PUBLIC_TOOL_NAMES
        or tuple(item.id for item in registry.exports) != (_STAGE8_EXPORT_ID,)
    ):
        raise RegistryError("Canonical registry cannot reproduce the Stage 5 profile")

    raw = copy.deepcopy(dict(registry.raw))
    raw["schema_version"] = "1.1.0"
    raw["registry_version"] = "2.3.0"
    raw["jobs"] = []
    datasets, raw = _export_free_dataset_projection(registry.datasets, raw)
    datasets, dashboard, raw = _stage1_dashboard_projection(
        datasets, raw
    )
    return replace(
        registry,
        schema_version="1.1.0",
        registry_version="2.3.0",
        datasets=datasets,
        jobs=(),
        exports=(),
        dashboard=dashboard,
        raw=raw,
        source_sha256=_STAGE5_REGISTRY_SOURCE_SHA256,
    )


def stage6_registry_profile(registry: Registry) -> Registry:
    """Project the canonical Stage 8 registry back to Stage 6 exactly."""

    registry = _stage8_input(registry)
    if (
        registry.schema_version != "1.4.0"
        or registry.registry_version != "2.6.0"
        or len(registry.migrations) != 30
        or len(registry.datasets) != 33
        or len(registry.collectors) != 19
        or tuple(str(item["id"]) for item in registry.tools) != PUBLIC_TOOL_NAMES
        or tuple(item.id for item in registry.jobs) != _STAGE7_JOB_IDS
        or tuple(item.id for item in registry.exports) != (_STAGE8_EXPORT_ID,)
    ):
        raise RegistryError("Canonical registry cannot reproduce the Stage 6 profile")

    raw = copy.deepcopy(dict(registry.raw))
    raw["schema_version"] = "1.2.0"
    raw["registry_version"] = "2.4.0"
    raw["jobs"] = []
    datasets, raw = _export_free_dataset_projection(registry.datasets, raw)
    return replace(
        registry,
        schema_version="1.2.0",
        registry_version="2.4.0",
        datasets=datasets,
        jobs=(),
        exports=(),
        raw=raw,
        source_sha256=_STAGE6_REGISTRY_SOURCE_SHA256,
    )


def stage7_registry_profile(registry: Registry) -> Registry:
    """Project the Stage 8 registry back to the immutable Stage 7 contract."""

    registry = _stage8_input(registry)
    if (
        registry.schema_version != "1.4.0"
        or registry.registry_version != "2.6.0"
        or len(registry.migrations) != 30
        or len(registry.datasets) != 33
        or len(registry.collectors) != 19
        or tuple(str(item["id"]) for item in registry.tools) != PUBLIC_TOOL_NAMES
        or tuple(item.id for item in registry.jobs) != _STAGE7_JOB_IDS
        or tuple(item.id for item in registry.exports) != (_STAGE8_EXPORT_ID,)
    ):
        raise RegistryError("Canonical registry cannot reproduce the Stage 7 profile")

    raw = copy.deepcopy(dict(registry.raw))
    raw["schema_version"] = "1.3.0"
    raw["registry_version"] = "2.5.0"
    datasets, raw = _export_free_dataset_projection(registry.datasets, raw)
    return replace(
        registry,
        schema_version="1.3.0",
        registry_version="2.5.0",
        datasets=datasets,
        exports=(),
        raw=raw,
        source_sha256=_STAGE7_REGISTRY_SOURCE_SHA256,
    )
