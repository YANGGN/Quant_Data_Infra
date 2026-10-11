"""Strict loader for the canonical four-store system registry."""

from __future__ import annotations

from .tool_platform.price_basis_versions import PRICE_BASIS_VERSIONS, NEW_PRICE_BASIS_POLICIES, remove_price_basis_policies

import copy
import hashlib
import json
import re
from collections import OrderedDict as _OrderedDict
from dataclasses import dataclass, replace
from threading import RLock as _RLock
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .errors import Issue, RegistryError, ValidationError
from .json_codec import MAX_JSON_BYTES, dumps_strict, loads_strict

# Host-owned registry metadata grows independently of public JSON request limits.
_MAX_REGISTRY_BYTES = 16 * 1024 * 1024
from .schema import validate_schema
from .stores import STORE_ROLES
from .tool_platform.catalog import (
    ADDITIVE_DATA_STATUS_TOOLS,
    ADDITIVE_ETF_TOOLS,
    ADDITIVE_NEWS_RESEARCH_TOOLS,
    ADDITIVE_PUBLIC_TOOL_NAMES,
    ADDITIVE_STAGE10_STATISTICS_TOOLS,
    CATALOG_ID,
    CATALOG_VERSION,
    CURRENT_FAMILY_COUNTS,
    CURRENT_PUBLIC_TOOL_NAMES,
    ADDITIVE_FMP_RESEARCH_TOOLS,
    ADDITIVE_TRANSCRIPT_TOOLS,
    ADDITIVE_TRANSCRIPT_RESEARCH_TOOLS,
    ADDITIVE_MONITOR_TOOLS,
    ADDITIVE_THETA_TOOLS,
    ADDITIVE_RETAINED_RESEARCH_TOOLS,
    ADDITIVE_FORWARD_PE_TOOLS,
    ADDITIVE_FORWARD_PE_ANALYSIS_TOOLS,
    FAMILY_COUNTS,
    LEGACY_TOOL_NAMES,
    OPERATION_GRAPH_IDS,
    PUBLIC_TOOL_NAMES,
    SCHEMA_DIALECT,
    VERSIONED_CATALOG_ID,
    VERSIONED_CATALOG_VERSION,
    VERSIONED_CANONICAL_MACRO_TOOLS,
    VERSIONED_COMPANY_FILING_TOOLS,
    VERSIONED_COMPANY_SHARE_COUNT_TOOLS,
    VERSIONED_DATA_QUALITY_TOOLS,
    VERSIONED_ECONOMETRICS_TOOLS,
    VERSIONED_INVESTMENT_ANALYSIS_TOOLS,
    VERSIONED_ETF_SNAPSHOT_TOOLS,
    VERSIONED_MARKET_INSTRUMENT_SEARCH_TOOLS,
    VERSIONED_NEWS_TOOLS,
    VERSIONED_OPTIONS_ACCESS_TOOLS,
    VERSIONED_MARKET_RETURN_TOOLS,
    VERSIONED_OPERATION_GRAPH_IDS,
    VERSIONED_RESEARCH_ANALYTIC_TOOLS,
    VERSIONED_TIMESERIES_ANALYSIS_TOOLS,
    VERSIONED_TOOL_NAMES,
    build_additive_tool_entries,
    build_current_tool_entries,
    build_tool_version_policies,
    current_tool_profiles,
    legacy_tool_entry,
    tool_profiles,
)


CANONICAL_REGISTRY_PATH = Path("config/system_registry.json")
REGISTRY_ENV = "QUANT_SYSTEM_REGISTRY_PATH"
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_STABLE_IDENTIFIER = re.compile(r"^[a-z0-9]+(?:[.:_-][a-z0-9]+)*$")
_HANDLER = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_DURATION = re.compile(r"^P(?:0D|[1-9][0-9]*D)$")
_PROHIBITED_TOOL_KEYS = {
    "database_path",
    "db_path",
    "sqlite_uri",
    "sql",
    "pragma",
    "filesystem_root",
    "connection_string",
}
_PROHIBITED_DASHBOARD_KEYS = {
    "connection_string",
    "database_path",
    "db_path",
    "filesystem_root",
    "pragma",
    "relation",
    "sql",
    "sqlite_uri",
}
_MUTABLE_IDENTITY_FIELDS = {
    "active",
    "completed_at",
    "current_version_id",
    "last_semantic_identity",
    "last_successful_run_id",
    "status",
    "updated_at",
}


STAGE1_TOOL_NAMES = LEGACY_TOOL_NAMES
_STAGE1_HANDLERS = {name: name for name in STAGE1_TOOL_NAMES}

_STAGE2_MIGRATION_IDS = frozenset(
    {
        "market:0001_foundation",
        "market:0002_vertical_slice",
        "market:0003_control_plane",
        "macro:0001_foundation",
        "macro:0002_vertical_slice",
        "macro:0003_control_plane",
        "company:0001_foundation",
        "company:0002_control_plane",
        "news:0001_foundation",
        "news:0002_control_plane",
    }
)
_STAGE2_DATASET_IDS = frozenset(
    {
        "fixture.market.daily_price_evidence",
        "fixture.market.instruments",
        "fixture.market.daily_prices",
        "fixture.macro.rtdsm_employ_evidence",
        "fixture.macro.rtdsm_employ",
    }
)
_STAGE2_COLLECTOR_IDS = frozenset(
    {
        "fixture.market.daily_price_import",
        "fixture.macro.rtdsm_employ_import",
    }
)


_STAGE3_MIGRATION_IDS = frozenset(
    {
        *_STAGE2_MIGRATION_IDS,
        "market:0004_instrument_catalog",
        "market:0005_instrument_classifications",
        "market:0006_controlled_universes",
        "macro:0004_stage3_core",
        "macro:0005_gdp_vintages",
        "macro:0006_treasury_yield_curves",
        "macro:0007_economic_calendar",
        "macro:0008_soma_summary_only",
        "macro:0009_eia_electricity_retail",
        "macro:0010_eia_weekly_fundamentals",
        "macro:0011_us_recession_periods",
    }
)
_STAGE3_DATASET_IDS = frozenset(
    {
        *_STAGE2_DATASET_IDS,
        "fixture.market.catalog_evidence",
        "fixture.market.instrument_classifications",
        "fixture.market.controlled_universes",
        "fixture.macro.stage3_catalog",
        "fixture.macro.gdp_vintages",
        "fixture.macro.treasury_yield_curves",
        "fixture.macro.economic_calendar",
        "fixture.macro.soma_evidence",
        "fixture.macro.soma_summary",
        "fixture.macro.eia_retail_evidence",
        "fixture.macro.eia_retail",
        "fixture.macro.eia_weekly_evidence",
        "fixture.macro.eia_weekly",
        "fixture.macro.recession_periods",
    }
)
_STAGE3_COLLECTOR_IDS = frozenset(
    {
        *_STAGE2_COLLECTOR_IDS,
        "fixture.market.catalog_import",
        "fixture.macro.gdp_import",
        "fixture.macro.treasury_import",
        "fixture.macro.calendar_import",
        "fixture.macro.soma_import",
        "fixture.macro.eia_retail_import",
        "fixture.macro.eia_weekly_import",
        "fixture.macro.recession_import",
        "fixture.macro.bls_import",
        "fixture.macro.bis_import",
        "fixture.macro.chicago_fed_import",
        "fixture.macro.bea_import",
    }
)


_STAGE4_MIGRATION_IDS = frozenset(
    {
        *_STAGE3_MIGRATION_IDS,
        "market:0007_options_core",
        "market:0008_option_surface_inputs",
        "company:0003_sec_core",
        "company:0004_corporate_actions",
        "company:0005_corporate_action_integrity",
        "company:0006_earnings_expectations",
        "company:0007_filing_issuer_view",
        "news:0003_immutable_items",
        "news:0004_search_index",
    }
)

_STAGE4_DATASET_IDS = frozenset(
    {
        *_STAGE3_DATASET_IDS,
        "fixture.market.option_capture_evidence",
        "fixture.market.options",
        "fixture.company.sec_evidence",
        "fixture.company.issuers",
        "fixture.company.filings",
        "fixture.company.fundamentals",
        "fixture.company.action_evidence",
        "fixture.company.corporate_actions",
        "fixture.company.expectation_evidence",
        "fixture.company.expectations",
        "fixture.company.filing_issuer_membership",
        "fixture.news.evidence",
        "fixture.news.items",
        "fixture.news.search_index",
    }
)

_STAGE4_COLLECTOR_IDS = frozenset(
    {
        *_STAGE3_COLLECTOR_IDS,
        "fixture.market.options_import",
        "fixture.company.sec_import",
        "fixture.company.actions_import",
        "fixture.company.expectations_import",
        "fixture.news.import",
    }
)

_ALPACA_SPY_OPTION_COLLECTOR_ID = "alpaca.market.spy_option_surface"
_ALPACA_SPY_OPTION_DATASET_IDS = (
    "fixture.market.instruments",
    "fixture.market.option_capture_evidence",
    "fixture.market.options",
)
_ALPACA_ETF_OPTION_COLLECTOR_ID = "alpaca.market.etf_option_surface_grid"
_ALPACA_ETF_OPTION_DATASET_IDS = _ALPACA_SPY_OPTION_DATASET_IDS

_IWM_ETF_DAILY_HISTORY_COLLECTOR_ID = "fmp.market.iwm_etf_daily_history"
_IWM_ETF_DAILY_HISTORY_DATASET_IDS = (
    "market.stage10.source_evidence",
    "market.stage10.instruments",
    "market.stage10.universes",
    "market.stage10.daily_prices",
)


_IWM_ETF_DAILY_HISTORY_BACKFILL_COLLECTOR_ID = (
    "fmp.market.iwm_etf_daily_history_backfill"
)
_IWM_ETF_DAILY_HISTORY_BACKFILL_DATASET_IDS = (
    "market.stage10.source_evidence",
    "market.stage10.daily_prices",
)
_SEC_AAPL_COMPANYFACTS_COLLECTOR_ID = "sec.company.aapl_fundamentals"
_SEC_AAPL_COMPANYFACTS_DATASET_IDS = (
    "fixture.company.sec_evidence",
    "fixture.company.issuers",
    "fixture.company.filings",
    "fixture.company.fundamentals",
    "fixture.company.filing_issuer_membership",
)
_SEC_MARKET_COMPANYFACTS_COLLECTOR_ID = "sec.company.market_fundamentals"
_SEC_MARKET_COMPANYFACTS_DATASET_IDS = _SEC_AAPL_COMPANYFACTS_DATASET_IDS

_STAGE1_DASHBOARD_ID = "stage1.overview"
_STAGE5_REGISTRY_SOURCE_SHA256 = (
    "56c2e8c97623c23596c92b177ee5e0aa89ecf164c37094216142cc82fc78cd9a"
)
_STAGE6_REGISTRY_SOURCE_SHA256 = (
    "def8c81264379493f9ce0ac2a864562a64106d3c3c42a640f9b05c882c2f3113"
)
_STAGE7_REGISTRY_SOURCE_SHA256 = (
    "643f4fd9a21f2b8b2701b0a408cddb63ae198175bc2cd61ce1ed637a9419a52c"
)
_STAGE8_REGISTRY_SOURCE_SHA256 = (
    "f4f565db7638ef026859af3f3b68b967ca54a80d1e09027d3afd1260cf53c10b"
)
_STAGE9_REGISTRY_SOURCE_SHA256 = (
    "46ff0f92f92380c203aacaf54e511ed219f3bc43edaaba0fb5a6fbe29b95a145"
)
_STAGE10_REGISTRY_SOURCE_SHA256 = (
    "c64aceefcc9a37cc0669398ea4d5817d997a5d82167941a204c2b77fffce77f9"
)
_STAGE11_REGISTRY_SOURCE_SHA256 = (
    "7e8ec6fc38d5a24962460d9c78a4df7754d3b29ad4b74c0c5e8ae807cd59ed56"
)
_STAGE12_REGISTRY_SOURCE_SHA256 = (
    "f65f7d039b73037012c6183c34501027f4298376ab94125e856e9dd79c6d234d"
)
_STAGE12A_REGISTRY_SOURCE_SHA256 = (
    "80a41e9f124cccb85bbdda665e33b1ef408f538b7e50b499b630b5ce6c761e7e"
)
_STAGE12B_REGISTRY_SOURCE_SHA256 = (
    "b39057d548d6ced6e7c0663ffafbee7e0c16c88a94baced584ff6949da7766f6"
)
_STAGE12C_REGISTRY_SOURCE_SHA256 = (
    "c24398b35bc9fe9553dd85346dd3879abd6b802e1dd255ffb186d4ed9e1ea769"
)
_PRE_MACRO_VINTAGE_REGISTRY_SOURCE_SHA256 = (
    "b6cfa9db720f4cb878127eae001e6bc1d59e0c7421a8c65277b24ab8fe36adb6"
)
_PRE_EMPLOYMENT_VINTAGE_REGISTRY_SOURCE_SHA256 = (
    "8da35a5a21ecd097d62fe625bdb096ecf3f2eba62413346957372ea81b9d7992"
)
_PRE_MACRO_HISTORY_REGISTRY_SOURCE_SHA256 = (
    "6040e45faa3ddbe522a97921a36e3a1f5ded34b3a4da7dd0ee32d52929e1fcb2"
)
_PRE_FMP_GDP_CPI_CALENDAR_REGISTRY_SOURCE_SHA256 = (
    "177908e080573f051231c9f89b2bb2967e12fd1a3cc2b20754ce86c7d8a216e6"
)
_PRE_FMP_EMPLOYMENT_CALENDAR_REGISTRY_SOURCE_SHA256 = (
    "f76a61027045c4408c60e57dacfcb5a17c617de4099395b1c6a62790e4019c4e"
)
_PRE_FMP_WHOLESALE_CALENDAR_REGISTRY_SOURCE_SHA256 = (
    "3c1caec6eedfb7e179d8a1f8e291ef6e8971da5f4539946ae76691ac53f22647"
)
_PRE_FMP_TREASURY_YIELD_CURVE_REGISTRY_SOURCE_SHA256 = (
    "b16724532234c3c1082326bdd2f0957c8518508849bcc5d090728fcfd2e3a8cd"
)
_PRE_NYFED_OVERNIGHT_RATES_REGISTRY_SOURCE_SHA256 = (
    "1c672f602735af44d19abb5d678492c053fac7d001b17e0aba2d6ed398689c08"
)
_PRE_NYFED_REPO_FACILITIES_REGISTRY_SOURCE_SHA256 = (
    "62a8f72f9b565f5b81eefce3bbef6704d5c9c65bd1222c19d49e6b53f54e4779"
)
_PRE_NYFED_SOMA_REGISTRY_SOURCE_SHA256 = (
    "a6fa1cc606182743ddcb8d6b0cc06b236e3dc5ba4891d71627bf5d1454a8c863"
)
_PRE_OFFICIAL_CONDITIONS_REGISTRY_SOURCE_SHA256 = (
    "999ff3ef57e2c858139e2c1122e9d2409d53d66dd6f1cdcd7cb624e08542f969"
)
_PRE_NYFED_CMDI_REGISTRY_SOURCE_SHA256 = (
    "9be40d07a7984b223303174a079b19a2a57fa7fcb5a56678482533dc53e4f8ff"
)
_PRE_OFFICIAL_MACRO_EXTENSION_REGISTRY_SOURCE_SHA256 = (
    "131b4f24b2e8d7d9dfa7fedb5de37cf16455aa5a0ee91d0aae69315fd0ac2aef"
)
_PRE_BLS_PRICE_WAGE_PRODUCTIVITY_REGISTRY_SOURCE_SHA256 = (
    "d7a5ba0a556abc9faa6d726e162969d4c11f0a5da614865eff23318d16ca55ff"
)
_PRE_GDI_VINTAGE_REGISTRY_SOURCE_SHA256 = (
    "4945c54e695b093112e6e7425dc214e5288396cf309d23ccf1aba33e386ee1e6"
)
_GDI_VINTAGE_REGISTRY_SOURCE_SHA256 = (
    "d28298c36ce6b418ec516845ac3f58c8b9e4c0706afdacbb5f752ed7d5431bd0"
)
_PRE_MARKET_RETURN_V2_REGISTRY_SOURCE_SHA256 = (
    "d28298c36ce6b418ec516845ac3f58c8b9e4c0706afdacbb5f752ed7d5431bd0"
)
_MARKET_RETURN_V2_REGISTRY_SOURCE_SHA256 = (
    "4b57a6bfb311001eee852f19ca45ad500095c7f997d804860cdd24a5ef24565d"
)
_PRE_TIMESERIES_ANALYSIS_V2_REGISTRY_SOURCE_SHA256 = (
    "4b57a6bfb311001eee852f19ca45ad500095c7f997d804860cdd24a5ef24565d"
)
_PRE_TIMESERIES_ANALYSIS_V2_CATALOG_SOURCE_SHA256 = (
    "2697d9abc49880a156b7d607d59aa6903f518fbd16d8d764adc0b8c0b257415b"
)
_TIMESERIES_ANALYSIS_V2_REGISTRY_SOURCE_SHA256 = (
    "eae80b10840afa2b724215aa431e1b8feab303486bbc87a60a8591e5812536cb"
)
_PRE_ECONOMETRICS_V2_REGISTRY_SOURCE_SHA256 = (
    "eae80b10840afa2b724215aa431e1b8feab303486bbc87a60a8591e5812536cb"
)
_PRE_ECONOMETRICS_V2_CATALOG_SOURCE_SHA256 = (
    "3ae30774d87c31217204da2240a56124c2e732a14f9fb6e38a57e3d6d7341b79"
)
_ECONOMETRICS_V2_REGISTRY_SOURCE_SHA256 = (
    "9ed2affcaa84c6420c7650c10a02361fad2bd892b33a5df2797e033e300ef86d"
)
_BEA_PERSONAL_INCOME_REGISTRY_SOURCE_SHA256 = (
    "a5e5b11bd9578428430c96f9eec59214e39f8cbb85f5b74fffc37aebaabdc27e"
)
_PRE_ROBUST_ECONOMETRICS_V21_REGISTRY_SOURCE_SHA256 = (
    "a5e5b11bd9578428430c96f9eec59214e39f8cbb85f5b74fffc37aebaabdc27e"
)
_PRE_ROBUST_ECONOMETRICS_V21_CATALOG_SOURCE_SHA256 = (
    "5250c18b70066de734cb2a4715ec20825f4a587892ba70728065dde82a4a239c"
)
_ROBUST_ECONOMETRICS_V21_REGISTRY_SOURCE_SHA256 = (
    "5c9702d8ca4c2f896083471cc60adecfd3a43c72d45694d04688ab07e6330035"
)
_ROBUST_ECONOMETRICS_V21_CATALOG_SOURCE_SHA256 = (
    "7de5126d50c438d0bb35cb82a9a0dd282251de3acceeb850d69ca20f894ef7b9"
)
_STATIONARITY_V21_REGISTRY_SOURCE_SHA256 = (
    "2a2b611ac6f752e6d83a81369155454b1484b8caebf4d1aaa3be60c41f0e866b"
)
_STATIONARITY_V21_CATALOG_SOURCE_SHA256 = (
    "529d904d46003c53785926730279dc4c37bf09b7d15f99c366122c416c4af26e"
)
_STRUCTURAL_BREAKS_V2_REGISTRY_SOURCE_SHA256 = (
    "3d6c0f31f2c72c20e5459c4e7f2358ea273437d59af99ef017b1f3ad47b1b547"
)
_STRUCTURAL_BREAKS_V2_CATALOG_SOURCE_SHA256 = (
    "e0650e5b76ec9a851220c2395c34f9172f1380b5a5c9b2bc681056c655bdeb5b"
)
_ECONOMETRICS_MODEL_SUITE_V3_REGISTRY_SOURCE_SHA256 = (
    "f7f445a3dbc991ca7b309ce306e5bc439403d929b96fb9931a22c036cb0eea85"
)
_PRE_FMP_CALENDAR_INCREMENTAL_REGISTRY_SOURCE_SHA256 = (
    "f7f445a3dbc991ca7b309ce306e5bc439403d929b96fb9931a22c036cb0eea85"
)
_FMP_CALENDAR_INCREMENTAL_REGISTRY_SOURCE_SHA256 = (
    "72516749f56f962bea2a265ef4a917558ef6e757444ae5d679bc50d2d64e6ed7"
)
_MARKET_PRICE_SERIES_V1_REGISTRY_SOURCE_SHA256 = (
    "835fe846c0d0bf0ce630cda0dd23588983c83f6fad663cbe51202fe00deee2a3"
)
_MARKET_PRICE_SERIES_V1_CATALOG_SOURCE_SHA256 = (
    "0f89da921b37d210c596646b3c4f47e4dbe27c2fe2579d8772c6db2bed312398"
)
_MARKET_AVAILABLE_TICKER_V1_REGISTRY_SOURCE_SHA256 = (
    "1ab956e8338b864873f25e6e41cc6a18ba9a271a1951bec0bdccf32a07b8fd2b"
)
_MARKET_AVAILABLE_TICKER_V1_CATALOG_SOURCE_SHA256 = (
    "554873c79f58abbee6f4fbf83e4a6767153c9ff920651825d8cac3bd6075905f"
)
_ALPACA_SPY_OPTION_REGISTRY_SOURCE_SHA256 = (
    "841041060550eeb41fed491c19835f77d278cbf68daaa9a7b2f754c3f9c4f0ff"
)
_SEC_AAPL_COMPANYFACTS_REGISTRY_SOURCE_SHA256 = (
    "af6545258751f7b7a7e7c68c673e18a36c65762809032db6ea540b33f249c182"
)
_SEC_MARKET_COMPANYFACTS_REGISTRY_SOURCE_SHA256 = (
    "f151db20dd26fe2123e887415736431cfe1dcda8bf8f42d83a8b47ad3a27fec2"
)
_CANONICAL_ACCESS_REGISTRY_SOURCE_SHA256 = (
    "b5236b88a2b320628b870fe3abe7898b76fa5fc1d527a223f87985963e38e264"
)
_CANONICAL_ACCESS_CATALOG_SOURCE_SHA256 = (
    "e6fa88fa63856247ab00073a14ff1321cfafa05d5323a22c26008e81d0b1eb5c"
)
_ANALYTICS_FOUNDATION_REGISTRY_SOURCE_SHA256 = (
    "eefa1288e8007518d466a3d4820522ae113ae6c52dc6df0e448cd3654de4a1b8"
)
_ANALYTICS_FOUNDATION_CATALOG_SOURCE_SHA256 = (
    "381a78aa59682fbf36cc90acc146d2cfa5b43ea90537b7356eca701df0794fbf"
)
_TECHNICAL_INDICATORS_V2_REGISTRY_SOURCE_SHA256 = (
    "3709c16168e2959a946c78e99c50b540b860d5f26ccf4afc3434831b8e9d8524"
)
_ALPACA_ETF_OPTION_REGISTRY_SOURCE_SHA256 = (
    "6d34dc495de10de42765e8909e30df744f69ad72d1901259f7da67be2d2710e1"
)
_IWM_ETF_DAILY_HISTORY_REGISTRY_SOURCE_SHA256 = (
    "0adc78cbe419b18ece989c9cdd6d918ef13113fa5f573d6f5fbe045b9eca8259"
)
_IWM_ETF_DAILY_HISTORY_BACKFILL_REGISTRY_SOURCE_SHA256 = (
    "1d8485bd1df5351d94f0b13f2264828640d40c756c6241503f40a7b7f4e46c43"
)
_STAGE10_RESEARCH_ANALYTICS_REGISTRY_SOURCE_SHA256 = (
    "87c74bbc26ce6101ff6136eefe9fdab0d9616f06d714d049d4c30287eda78323"
)
_STAGE10_RESEARCH_ANALYTICS_CATALOG_SOURCE_SHA256 = (
    "3c15ebcbf145d188681a49016dc621ffc96a48f6480f7ff9067d962c8fa29693"
)
_COMPANY_FILING_PAGINATION_REGISTRY_SOURCE_SHA256 = (
    "c201524e4e4a72b5377d36390e0cc5c746d392674b598ac1499ab818b418d238"
)
_COMPANY_FILING_PAGINATION_CATALOG_SOURCE_SHA256 = (
    "b313cc2c4e4fd39311c00a5c18ae3ef8aa4de157f58f23c0837b52a51d601ac5"
)
_PLACEHOLDER_SUCCESSOR_REGISTRY_SOURCE_SHA256 = (
    "f40c4d2e0cad90f686bffe52116d138f3b578f33f4844245682387d398a19e01"
)
_PLACEHOLDER_SUCCESSOR_CATALOG_SOURCE_SHA256 = (
    "a70903e9ba65fd71d5d79775698174dafea630c58c435bb4d8fcc504320aa05b"
)
_TECHNICAL_INDICATORS_V21_REGISTRY_SOURCE_SHA256 = (
    "cec35d5cfed9f25c40f5adfa2f2581b0de8d442a75202e687f791b3a16dc3d7f"
)
_TECHNICAL_INDICATORS_V21_CATALOG_SOURCE_SHA256 = (
    "65311bb28efe62651ecb3420f0be13fd413df468487141cca0972d45e7684c85"
)
_TECHNICAL_INDICATORS_V22_REGISTRY_SOURCE_SHA256 = (
    "9782e77c530251401e9b5e42b33115949ce8bb28753f0a0471fa2e8353dd8802"
)
_TECHNICAL_INDICATORS_V22_CATALOG_SOURCE_SHA256 = (
    "cb1c6965582b90eaeec27054eda0d66e7a4c603aefa69f9b453ae342d779302b"
)
_TECHNICAL_INDICATORS_V23_REGISTRY_SOURCE_SHA256 = (
    "02a4aeb34632c5ae194622b7de77ec31145135bdf399620e45af461132134d43"
)
_TECHNICAL_INDICATORS_V23_CATALOG_SOURCE_SHA256 = (
    "4803071d5cb2ababa2710ac4c0e3eb1e1640b06aa0b6071e14c2f40be68fec72"
)
_INVESTMENT_ANALYSIS_V2_REGISTRY_SOURCE_SHA256 = (
    "1d36ddd20494cfc0ae9e6172f662c5dfe04b905319c0f83b439446f44fd29b5e"
)
_REGISTRY_259_ADDITIVE_SUCCESSORS_SOURCE_SHA256 = (
    "0457910706181d7f4d9bb07efbf18845dd86335999b55c5d5a8d203041d315a1"
)
_REGISTRY_259_ADDITIVE_SUCCESSORS_CATALOG_SOURCE_SHA256 = (
    "e4fdb9d9b6783ec131838762d8e2ae4b1c6833cc37c0292e1ceed09e0d919474"
)
_TECHNICAL_INDICATORS_V25_REGISTRY_SOURCE_SHA256 = (
    "563c4294c47d534754014dc77a4a1b39e84749cc4d8165636bafd82feff679b0"
)
_TECHNICAL_INDICATORS_V25_CATALOG_SOURCE_SHA256 = (
    "cebc1ca58257fed167db36973be1ce37e6e01162c2bc072c24591a673b0aedb4"
)
_TECHNICAL_INDICATORS_V26_REGISTRY_SOURCE_SHA256 = (
    "0f43045c1dcc46b0f9d5ecb0aaf98aa3e9ad61a43e250afc389408602a8615ea"
)
_TECHNICAL_INDICATORS_V26_CATALOG_SOURCE_SHA256 = (
    "b70798594de6149b7710b36d3b735b3d24ff81eb23277ba54dfdc697aceb0efc"
)
_TECHNICAL_INDICATORS_V27_REGISTRY_SOURCE_SHA256 = (
    "59db17edd70d8ae9aa77f1468cf7c459338e3d1dff0015b3c99853b2e1e12fd2"
)
_TECHNICAL_INDICATORS_V27_CATALOG_SOURCE_SHA256 = (
    "18e86daf6ef3291407ab794d7f53015c80bb90c88f2518891f7b904002f515a1"
)
_CURRENT_NEWS_REGISTRY_SOURCE_SHA256 = (
    "06466e9b79be5bc0fab927a81b5972059bbad34ba4a674c1c456c3eaeaf04d72"
)
_CURRENT_NEWS_CATALOG_SOURCE_SHA256 = (
    "05cfbfb29b544594a3b176daeca659470f3c91a20a423c730d8da9622c8cae2d"
)
_CURRENT_MULTI_SOURCE_REGISTRY_SOURCE_SHA256 = (
    "b47b6ad63ecaa41477388af033c7f928083ceb5e7db17bf76ff4ab99f71f3dc4"
)
_CURRENT_MULTI_SOURCE_CATALOG_SOURCE_SHA256 = (
    "6a4f7e8ce223658617512928b860f5cf5bde85e01f075070771fa019e882ed46"
)
_OPTION_RAW_EVIDENCE_REGISTRY_SOURCE_SHA256 = (
    "c22d9ada8be3c3c7f9538c902bac3ef3467b9fdfa43c23fd7aa1c88200d58614"
)
_NEWS_RESEARCH_REGISTRY_SOURCE_SHA256 = (
    "f7b8c402ce4abce5d024f7fcdc8debde97e25324739f037ef209312bb4d070f3"
)
_NEWS_RESEARCH_CATALOG_SOURCE_SHA256 = (
    "e35b136e3e47a6211a85d62c75baf6ecd52b9246938a30549ace5c19e0c39700"
)
_DATA_STATUS_OPTIONS_REGISTRY_SOURCE_SHA256 = (
    "a80b0e06db95968c9fd49cd3d90054b709c57895993a28b512ba2550e162f325"
)
_MACRO_DATABASE_EXPANSION_REGISTRY_SOURCE_SHA256 = (
    "9b59f6b643e4cff7390559763c8532215ac9927a1f3119870385127af3a6a27e"
)
_DATA_STATUS_OPTIONS_CATALOG_SOURCE_SHA256 = (
    "fcfb29de2c2138995918e40c603704a0b2df4c17b6c3229312734bb46b0f2a28"
)
_INVESTMENT_ANALYSIS_V2_CATALOG_SOURCE_SHA256 = (
    "5c0ea96b9aba9d73f8f89aea20a052c858691e10a1047f22594f027d8f893101"
)
_PLACEHOLDER_SUCCESSOR_VERSIONED_TOOL_IDS = frozenset(
    (
        *VERSIONED_COMPANY_SHARE_COUNT_TOOLS,
        *VERSIONED_MARKET_INSTRUMENT_SEARCH_TOOLS,
    )
)
_TECHNICAL_INDICATORS_V2_CATALOG_SOURCE_SHA256 = (
    "864a4d07afbf2558a331d30275cf21f4e31521142ee2d9d010dc26cc5680a757"
)
_PRE_FMP_RESEARCH_TOOL_NAMES = tuple(name for name in CURRENT_PUBLIC_TOOL_NAMES if name not in (*ADDITIVE_FMP_RESEARCH_TOOLS, *ADDITIVE_TRANSCRIPT_TOOLS, *ADDITIVE_FORWARD_PE_TOOLS, *ADDITIVE_FORWARD_PE_ANALYSIS_TOOLS, *ADDITIVE_TRANSCRIPT_RESEARCH_TOOLS, *ADDITIVE_THETA_TOOLS, *ADDITIVE_RETAINED_RESEARCH_TOOLS, *ADDITIVE_MONITOR_TOOLS))
_PRE_ETF_TOOL_NAMES = tuple(name for name in _PRE_FMP_RESEARCH_TOOL_NAMES if name not in ADDITIVE_ETF_TOOLS)
_PRE_DATA_STATUS_OPTIONS_TOOL_NAMES = tuple(
    name
    for name in _PRE_ETF_TOOL_NAMES
    if name not in ADDITIVE_DATA_STATUS_TOOLS
)
_PRE_NEWS_RESEARCH_TOOL_NAMES = tuple(
    name
    for name in _PRE_DATA_STATUS_OPTIONS_TOOL_NAMES
    if name not in ADDITIVE_NEWS_RESEARCH_TOOLS
)
_PRE_ANALYTICS_FOUNDATION_TOOL_NAMES = tuple(
    name
    for name in _PRE_NEWS_RESEARCH_TOOL_NAMES
    if name not in ADDITIVE_STAGE10_STATISTICS_TOOLS
)
_PRE_CANONICAL_ACCESS_TOOL_NAMES = tuple(
    name
    for name in _PRE_ANALYTICS_FOUNDATION_TOOL_NAMES
    if name not in {"macro.get_release_calendar", "market.get_volume_series"}
)
_PRE_MARKET_AVAILABLE_TICKER_TOOL_NAMES = tuple(
    name
    for name in _PRE_CANONICAL_ACCESS_TOOL_NAMES
    if name != "market.get_available_ticker"
)

_ECONOMETRICS_MODEL_SUITE_V3_CATALOG_SOURCE_SHA256 = (
    "2c9424a0ff9cda9130d559c2dacb6cef55ff9ff8537191285219d608411b7f24"
)

_VERSIONED_TOOL_NAMES_2_35 = (
    "market.get_returns",
    "market.get_forward_returns",
    "timeseries.describe",
    "timeseries.align",
    "timeseries.correlation",
    "econometrics.regression",
    "econometrics.rolling_regression",
    "econometrics.stationarity",
)
_VERSIONED_ECONOMETRICS_TOOLS_2_34 = (
    "econometrics.regression",
    "econometrics.rolling_regression",
    "econometrics.stationarity",
)
_POLICY_VARIANTS_2_35 = tuple(
    (name, ("2.0.0",)) for name in _VERSIONED_TOOL_NAMES_2_35
)
_POLICY_VARIANTS_2_36 = tuple(
    (name, ("2.0.0", "2.1.0"))
    if name in {"econometrics.regression", "econometrics.rolling_regression"}
    else (name, ("2.0.0",))
    for name in _VERSIONED_TOOL_NAMES_2_35
)
_POLICY_VARIANTS_2_37 = tuple(
    (name, ("2.0.0", "2.1.0")) if name.startswith("econometrics.") else (name, ("2.0.0",))
    for name in _VERSIONED_TOOL_NAMES_2_35
)
_POLICY_VARIANTS_2_38 = (
    *_POLICY_VARIANTS_2_37,
    ("econometrics.structural_breaks", ("2.0.0",)),
)
_POLICY_VARIANTS_2_39 = tuple(
    (name, versions + ("3.0.0",))
    if name == "econometrics.regression" else (name, versions)
    for name, versions in _POLICY_VARIANTS_2_38
)

_GDI_VINTAGE_MIGRATION_ID = "macro:0017_live_gdi_vintages"
_GDI_VINTAGE_MIGRATION_RESOURCE = (
    "quant_data/migrations/macro/0017_live_gdi_vintages.sql"
)
_GDI_VINTAGE_MIGRATION_SHA256 = (
    "e92da1620b2da10e15d76ee9a3817fc081d0363f6cd7b87653f2340a8f756e71"
)
_FMP_GDP_CPI_CALENDAR_COLLECTOR_ID = (
    "fmp.macro.gdp_cpi_release_calendar_history"
)
_FMP_EMPLOYMENT_CALENDAR_COLLECTOR_ID = (
    "fmp.macro.employment_release_calendar_refresh"
)
_FMP_GDP_CPI_CALENDAR_DATASET_ID = "fixture.macro.economic_calendar"
_FMP_GDP_CPI_OFFICIAL_DATASET_ID = "macro.official_vintages"
_FMP_GDP_CPI_SURPRISE_TOOL_ID = "macro.release_surprises"
_FMP_WHOLESALE_CALENDAR_MIGRATION_ID = (
    "macro:0016_fmp_calendar_wholesale_evidence"
)
_FMP_WHOLESALE_CALENDAR_DATASET_ID = "macro.fmp.economic_calendar_evidence"
_FMP_WHOLESALE_CALENDAR_COLLECTOR_ID = (
    "fmp.macro.us_economic_calendar_wholesale"
)
_FMP_WHOLESALE_CALENDAR_RELATIONS = (
    "fmp_economic_calendar_captures",
    "fmp_economic_calendar_rows",
)
_FMP_CALENDAR_INCREMENTAL_MIGRATION_ID = (
    "macro:0018_fmp_calendar_incremental_events"
)
_FMP_CALENDAR_INCREMENTAL_MIGRATION_RESOURCE = (
    "quant_data/migrations/macro/0018_fmp_calendar_incremental_events.sql"
)
_FMP_CALENDAR_INCREMENTAL_MIGRATION_SHA256 = (
    "078cfd62e7cd7a314e6b828b19414023f60fa81c760c89a31b3398f1b7e41a3e"
)
_FMP_CALENDAR_INCREMENTAL_EVIDENCE_DATASET_ID = (
    "macro.fmp.economic_calendar_incremental_evidence"
)
_FMP_CALENDAR_INCREMENTAL_EVENT_DATASET_ID = (
    "macro.fmp.economic_calendar_incremental_events"
)
_FMP_CALENDAR_INCREMENTAL_DATASET_IDS = (
    _FMP_CALENDAR_INCREMENTAL_EVIDENCE_DATASET_ID,
    _FMP_CALENDAR_INCREMENTAL_EVENT_DATASET_ID,
)
_FMP_TREASURY_YIELD_CURVE_COLLECTOR_ID = (
    "fmp.macro.treasury_yield_curve_history"
)
_FMP_TREASURY_YIELD_CURVE_DATASET_IDS = (
    "fixture.macro.rtdsm_employ_evidence",
    "fixture.macro.rtdsm_employ",
    "fixture.macro.stage3_catalog",
    "fixture.macro.treasury_yield_curves",
)
_NYFED_OVERNIGHT_RATES_COLLECTOR_ID = "nyfed.macro.overnight_rates_history"
_NYFED_OVERNIGHT_RATES_DATASET_IDS = (
    "fixture.macro.rtdsm_employ_evidence",
    "fixture.macro.rtdsm_employ",
    "fixture.macro.stage3_catalog",
)
_NYFED_REPO_FACILITIES_COLLECTOR_ID = (
    "nyfed.macro.repo_facility_usage_history"
)
_NYFED_REPO_FACILITIES_DATASET_IDS = (
    "fixture.macro.rtdsm_employ_evidence",
    "fixture.macro.rtdsm_employ",
    "fixture.macro.stage3_catalog",
)
_NYFED_SOMA_COLLECTOR_ID = "nyfed.macro.soma_summary_history"
_NYFED_SOMA_DATASET_IDS = (
    "fixture.macro.soma_evidence",
    "fixture.macro.soma_summary",
)
_OFFICIAL_CONDITIONS_COLLECTOR_IDS = (
    "federal_reserve.macro.h41_history",
    "chicagofed.macro.nfci_history",
    "bis.macro.credit_conditions_history",
)
_OFFICIAL_CONDITIONS_DATASET_IDS = (
    "fixture.macro.rtdsm_employ_evidence",
    "fixture.macro.rtdsm_employ",
    "fixture.macro.stage3_catalog",
)
_STAGE12B_COLLECTOR_ID = "market.stage12b.fmp_daily_incremental_fixture"
_STAGE12C_COLLECTOR_ID = "market.stage12c.fmp_daily_incremental_manual"
_NYFED_CMDI_COLLECTOR_ID = "nyfed.macro.cmdi_history"
_NYFED_CMDI_DATASET_IDS = _OFFICIAL_CONDITIONS_DATASET_IDS
_OFFICIAL_MACRO_EXTENSION_COLLECTOR_IDS = (
    "treasury_fiscal_data.macro.tga_closing_balance_history",
    "eia.macro.natural_gas_storage_history",
    "nber.macro.us_recession_history",
)
_OFFICIAL_MACRO_EXTENSION_DATASET_IDS = _OFFICIAL_CONDITIONS_DATASET_IDS
_BLS_PRICE_WAGE_PRODUCTIVITY_COLLECTOR_ID = (
    "bls.macro.price_wage_productivity_history"
)
_BLS_PRICE_WAGE_PRODUCTIVITY_DATASET_IDS = _OFFICIAL_CONDITIONS_DATASET_IDS
_BEA_PERSONAL_INCOME_COLLECTOR_ID = "bea.macro.personal_income_history"
_BEA_PERSONAL_INCOME_DATASET_IDS = _OFFICIAL_CONDITIONS_DATASET_IDS
_ETF_SNAPSHOT_REGISTRY_SOURCE_SHA256 = "4c2de9ef1ac49a4c23ab326000878fa66629caa1f8a0bcb65d4c089d827e9ac3"
_COMPANY_MARKET_REGISTRY_SOURCE_SHA256 = '2e9c3e4d2bfc263735a1e9c875d2091210065e0a375a0a0e0c420839a03c774f'
_COMPANY_MARKET_COLLECTOR_SPECS = {'fmp.company.corporate_actions_current': {'id': 'fmp.company.corporate_actions_current', 'version': '1.0.0', 'handler': 'company.fmp_corporate_actions_current', 'network': True, 'input_datasets': ['market.stage10.instruments', 'fixture.company.issuers'], 'output_datasets': ['fixture.company.action_evidence', 'fixture.company.corporate_actions'], 'semantic_identity': {'includes': ['request_scope', 'normalization_version', 'normalized_records'], 'excludes': ['api_key', 'captured_at', 'http_headers', 'source_row_order', 'raw_response_identity', 'discovery_evidence_identity', 'dividend_yield']}, 'mutation_policy': {'mode': 'append_versions_and_capture_membership', 'unchanged': 'zero_persistent_writes'}, 'workload_bounds': {'max_requests': 1, 'max_rows': 1000, 'max_bytes': 1048576, 'max_seconds': 30}, 'retry_policy': {'transient_classes': [], 'max_attempts': 1, 'backoff': 'none_single_attempt', 'honor_retry_after': False}, 'configuration_env': ['FMP_API_KEY'], 'physical_locks': 'derived_from_output_store_paths', 'schedule_eligibility': {'mode': 'manual_only'}}, 'fmp.company.analyst_estimates_current': {'id': 'fmp.company.analyst_estimates_current', 'version': '1.0.0', 'handler': 'company.fmp_analyst_estimates_current', 'network': True, 'input_datasets': ['market.stage10.instruments', 'fixture.company.issuers'], 'output_datasets': ['fixture.company.expectation_evidence', 'fixture.company.expectations'], 'semantic_identity': {'includes': ['request_scope', 'normalization_version', 'normalized_records'], 'excludes': ['api_key', 'captured_at', 'http_headers', 'source_row_order', 'raw_response_identity', 'discovery_evidence_identity', 'dividend_yield']}, 'mutation_policy': {'mode': 'append_versions_and_capture_membership', 'unchanged': 'zero_persistent_writes'}, 'workload_bounds': {'max_requests': 1, 'max_rows': 10, 'max_bytes': 1048576, 'max_seconds': 30}, 'retry_policy': {'transient_classes': [], 'max_attempts': 1, 'backoff': 'none_single_attempt', 'honor_retry_after': False}, 'configuration_env': ['FMP_API_KEY'], 'physical_locks': 'derived_from_output_store_paths', 'schedule_eligibility': {'mode': 'manual_only'}}}

_MACRO_DATABASE_EXPANSION_COLLECTOR_IDS = (
    "cftc.macro.tff_futures_only_history",
    "cftc.macro.disaggregated_futures_only_history",
    "treasury_fiscal_data.macro.securities_auctions_history",
    "nyfed.macro.primary_dealer_statistics_history",
    "federal_reserve.macro.h8_history",
    "federal_reserve.macro.sloos_history",
)
_MACRO_DATABASE_EXPANSION_DATASET_IDS = _OFFICIAL_CONDITIONS_DATASET_IDS
_MACRO_DATABASE_EXPANSION_COLLECTOR_SPECS: Mapping[
    str, tuple[str, int, int, int, int]
] = {
    "cftc.macro.tff_futures_only_history": (
        "macro.cftc_tff_futures_only_history", 8, 4_000, 16_777_216, 480
    ),
    "cftc.macro.disaggregated_futures_only_history": (
        "macro.cftc_disaggregated_futures_only_history",
        8,
        4_000,
        16_777_216,
        480,
    ),
    "treasury_fiscal_data.macro.securities_auctions_history": (
        "macro.treasury_securities_auctions_history",
        1,
        1_000,
        16_777_216,
        60,
    ),
    "nyfed.macro.primary_dealer_statistics_history": (
        "macro.nyfed_primary_dealer_statistics_history",
        33,
        100_000,
        67_108_864,
        180,
    ),
    "federal_reserve.macro.h8_history": (
        "macro.federal_reserve_h8_history", 7, 20_000, 16_777_216, 420
    ),
    "federal_reserve.macro.sloos_history": (
        "macro.federal_reserve_sloos_history",
        6,
        20_000,
        16_777_216,
        360,
    ),
}
_STAGE12C_OUTPUT_DATASET_IDS = (
    "market.stage10.source_evidence",
    "market.stage10.daily_prices",
)
_STAGE12C_INPUT_DATASET_IDS = (
    "market.stage10.instruments",
    "market.stage10.daily_prices",
)
_STAGE12C_EXPECTED_DATASET_COLLECTOR_IDS: Mapping[str, tuple[str, ...]] = {
    "market.stage10.source_evidence": (
        "fmp.market.stage10_universe_capture",
        "fmp.market.stage10_daily_history",
        _STAGE12B_COLLECTOR_ID,
        _STAGE12C_COLLECTOR_ID,
    ),
    "market.stage10.daily_prices": (
        "fmp.market.stage10_daily_history",
        _STAGE12B_COLLECTOR_ID,
        _STAGE12C_COLLECTOR_ID,
    ),
}
_STAGE12B_OUTPUT_DATASET_IDS = (
    "market.stage10.source_evidence",
    "market.stage10.daily_prices",
)
_STAGE12B_INPUT_DATASET_IDS = (
    "market.stage10.instruments",
    "market.stage10.daily_prices",
)
_STAGE12B_EXPECTED_DATASET_COLLECTOR_IDS: Mapping[str, tuple[str, ...]] = {
    "market.stage10.source_evidence": (
        "fmp.market.stage10_universe_capture",
        "fmp.market.stage10_daily_history",
        _STAGE12B_COLLECTOR_ID,
    ),
    "market.stage10.daily_prices": (
        "fmp.market.stage10_daily_history",
        _STAGE12B_COLLECTOR_ID,
    ),
}
_STAGE11_CANONICAL_RETRY_POLICY = {
    "transient_classes": ["connection", "timeout", "http_429", "http_5xx"],
    "max_attempts": 3,
    "backoff": "deterministic_1s_then_2s_no_jitter",
    "honor_retry_after": False,
}
_STAGE11_HISTORICAL_RETRY_POLICY = {
    "transient_classes": ["http_429", "http_500", "transport"],
    "max_attempts": 1,
    "backoff": "none_operator_resume",
    "honor_retry_after": False,
}
_STAGE9_COLLECTOR_ID = "fmp.market.daily_price_backfill"
_STAGE9_DATASET_IDS = frozenset(
    {
        "market.fmp.daily_price_evidence",
        "market.fmp.instruments",
        "market.fmp.daily_prices",
    }
)
_STAGE9_MIGRATION_ID = "market:0009_fmp_daily_price_backfill"
_STAGE10_COLLECTOR_IDS = frozenset(
    {
        "fmp.market.stage10_universe_capture",
        "fmp.market.stage10_daily_history",
    }
)
_STAGE10_DATASET_IDS = frozenset(
    {
        "market.stage10.source_evidence",
        "market.stage10.instruments",
        "market.stage10.universes",
        "market.stage10.daily_prices",
    }
)
_STAGE10_MIGRATION_ID = "market:0010_stage10_market_history"
_STAGE11_COLLECTOR_IDS = frozenset(
    {
        "bea.macro.stage11_nipa_history",
        "eia.macro.stage11_electricity_retail_history",
        "eia.macro.stage11_petroleum_weekly_stock_history",
    }
)
_STAGE11_DATASET_IDS = frozenset(
    {
        "macro.bea.nipa_history_evidence",
        "macro.bea.nipa_history",
        "macro.eia.electricity_retail_history_evidence",
        "macro.eia.electricity_retail_history",
        "macro.eia.petroleum_weekly_stock_history_evidence",
        "macro.eia.petroleum_weekly_stock_history",
    }
)
_STAGE11_MIGRATION_ID = "macro:0012_stage11_bea_eia_live_history"
_MACRO_VINTAGE_MIGRATION_ID = "macro:0013_live_gdp_cpi_vintages"
_MACRO_VINTAGE_DATASET_IDS = frozenset(
    {
        "macro.official_vintages_evidence",
        "macro.official_vintages",
    }
)
_MACRO_VINTAGE_COLLECTOR_IDS = frozenset(
    {
        "bea.macro.live_gdp_vintages",
        "bls.macro.live_cpi_vintages",
    }
)
_EMPLOYMENT_VINTAGE_MIGRATION_ID = "macro:0014_live_employment_vintages"
_EMPLOYMENT_VINTAGE_COLLECTOR_IDS = frozenset(
    {
        "philadelphia_fed.macro.live_employment_vintages",
        "bls.macro.live_employment_current",
    }
)
_MACRO_HISTORY_MIGRATION_ID = "macro:0015_live_macro_history_extension"
_MACRO_HISTORY_COLLECTOR_IDS = frozenset(
    {
        "bls.macro.cpi_current_history",
        "philadelphia_fed.macro.gdp_cpi_vintage_history",
    }
)
_STAGE11_DATASET_RELATIONS: Mapping[str, tuple[str, ...]] = {
    "macro.bea.nipa_history_evidence": ("stage11_bea_nipa_captures",),
    "macro.bea.nipa_history": (
        "stage11_bea_nipa_observation_versions",
        "stage11_bea_nipa_observations",
    ),
    "macro.eia.electricity_retail_history_evidence": (
        "stage11_eia_retail_captures",
    ),
    "macro.eia.electricity_retail_history": (
        "stage11_eia_retail_observation_versions",
        "stage11_eia_retail_observations",
    ),
    "macro.eia.petroleum_weekly_stock_history_evidence": (
        "stage11_eia_weekly_captures",
    ),
    "macro.eia.petroleum_weekly_stock_history": (
        "stage11_eia_weekly_observation_versions",
        "stage11_eia_weekly_observations",
    ),
}
_FMP_STOCK_LATEST_COLLECTOR_ID = "fmp.news.stock_latest"
_FMP_STOCK_LATEST_DATASET_IDS = frozenset(
    {
        "news.fmp.stock_latest_evidence",
        "news.fmp.stock_latest_articles",
    }
)
_FMP_STOCK_LATEST_MIGRATION_ID = "news:0005_fmp_stock_latest"
_FMP_STOCK_LATEST_DATASET_RELATIONS: Mapping[str, tuple[str, ...]] = {
    "news.fmp.stock_latest_evidence": (
        "fmp_stock_latest_attempts",
        "fmp_stock_latest_outcomes",
        "fmp_stock_latest_captures",
    ),
    "news.fmp.stock_latest_articles": (
        "fmp_stock_latest_articles",
        "fmp_stock_latest_article_versions",
        "fmp_stock_latest_capture_articles",
    ),
}
_FMP_STOCK_LATEST_CURRENT_COLLECTOR_ID = "fmp.news.stock_latest_current"
_FMP_STOCK_LATEST_CURRENT_DATASET_IDS = frozenset(
    {
        "news.fmp.stock_latest_current_evidence",
        "news.fmp.stock_latest_current_articles",
    }
)
_FMP_STOCK_LATEST_CURRENT_MIGRATION_ID = (
    "news:0006_fmp_stock_latest_current"
)
_FMP_STOCK_LATEST_CURRENT_DATASET_RELATIONS: Mapping[str, tuple[str, ...]] = {
    "news.fmp.stock_latest_current_evidence": (
        "fmp_stock_latest_current_attempts",
        "fmp_stock_latest_current_outcomes",
        "fmp_stock_latest_current_captures",
    ),
    "news.fmp.stock_latest_current_articles": (
        "fmp_stock_latest_current_articles",
        "fmp_stock_latest_current_article_versions",
        "fmp_stock_latest_current_capture_articles",
    ),
}
_CURRENT_MULTI_SOURCE_COLLECTOR_ID = "news.current_multi_source"
_CURRENT_MULTI_SOURCE_DATASET_IDS = frozenset(
    {
        "news.current_multi_source_evidence",
        "news.current_multi_source_articles",
    }
)
_CURRENT_MULTI_SOURCE_MIGRATION_ID = "news:0007_current_multi_source"
_LEGACY_FMP_NEWS_MIGRATION_ID = "news:0008_adopt_fmp_news_legacy"
_LEGACY_FMP_NEWS_DATASET_ID = "news.fmp.stock_latest_legacy_articles"
_CURRENT_MULTI_SOURCE_DATASET_RELATIONS: Mapping[str, tuple[str, ...]] = {
    "news.current_multi_source_evidence": (
        "current_multi_source_attempts",
        "current_multi_source_outcomes",
        "current_multi_source_captures",
    ),
    "news.current_multi_source_articles": (
        "current_multi_source_articles",
        "current_multi_source_article_versions",
        "current_multi_source_capture_articles",
        "current_multi_source_article_symbols",
    ),
}
_LEGACY_FMP_NEWS_DATASET_RELATIONS = {
    _LEGACY_FMP_NEWS_DATASET_ID: ("fmp_news_articles",),
}
_OPTION_RAW_EVIDENCE_MIGRATION_ID = "market:0011_option_raw_evidence"
_OPTION_RAW_EVIDENCE_DATASET_ID = "market.alpaca.option_raw_evidence"
_OPTION_RAW_EVIDENCE_RELATIONS = (
    "option_raw_responses",
    "option_capture_raw_responses",
)
_ALPACA_OPTION_COLLECTOR_IDS = (
    "alpaca.market.spy_option_surface",
    "alpaca.market.etf_option_surface_grid",
)
_STAGE7_JOB_IDS = (
    "news-hourly",
    "sec-daily",
    "options-close",
    "macro-daily",
    "market-close",
    "expectations",
    "company-weekly",
    "macro-monthly",
)
_STAGE7_JOB_LAYOUTS: Mapping[str, Mapping[str, Any]] = {
    "news-hourly": {
        "recovered_cadence": "hourly_at_minute_10",
        "collectors": ("fixture.news.import",),
        "success_marker": "none",
        "timeout_seconds": 60,
    },
    "sec-daily": {
        "recovered_cadence": "daily_at_0715",
        "collectors": ("fixture.company.sec_import",),
        "success_marker": "none",
        "timeout_seconds": 60,
    },
    "options-close": {
        "recovered_cadence": "weekdays_at_1320_and_1620_calendar_gated",
        "collectors": (
            "fixture.macro.treasury_import",
            "fixture.market.options_import",
        ),
        "success_marker": "none",
        "timeout_seconds": 90,
    },
    "macro-daily": {
        "recovered_cadence": "daily_at_1800",
        "collectors": ("fixture.macro.calendar_import",),
        "success_marker": "none",
        "timeout_seconds": 60,
    },
    "market-close": {
        "recovered_cadence": "daily_or_weekdays_at_1800",
        "collectors": ("fixture.market.daily_price_import",),
        "success_marker": "none",
        "timeout_seconds": 60,
    },
    "expectations": {
        "recovered_cadence": "weekdays_at_2000",
        "collectors": ("fixture.company.expectations_import",),
        "success_marker": "none",
        "timeout_seconds": 60,
    },
    "company-weekly": {
        "recovered_cadence": "saturday_at_0900",
        "collectors": ("fixture.company.actions_import",),
        "success_marker": "none",
        "timeout_seconds": 60,
    },
    "macro-monthly": {
        "recovered_cadence": "sunday_at_1100",
        "collectors": (
            "fixture.macro.gdp_import",
            "fixture.macro.eia_retail_import",
            "fixture.macro.recession_import",
        ),
        "success_marker": "monthly_full_success_only",
        "timeout_seconds": 120,
    },
}
_STAGE7_LIFECYCLE = {
    "state": "fixture_validated",
    "execution_mode": "manual_fixture_only",
    "scheduling_enabled": False,
}
_STAGE7_RECEIPT = {
    "schema_version": "1.0",
    "visibility": "private",
    "state_directory": "explicit",
    "publication": "atomic",
    "immutability": "immutable",
    "directory_mode": "0700",
    "file_mode": "0600",
}
_STAGE7_EXIT_CODE_POLICY = {
    "id": "stage7_sysexits_v1",
    "mapping": {
        "success": 0,
        "invalid_plan": 64,
        "unavailable": 69,
        "internal": 70,
        "io": 74,
        "temporary": 75,
        "configuration": 78,
        "timeout": 124,
    },
    "precedence": [74, 78, 64, 124, 70, 75, 69],
}
_STAGE7_AGGREGATE_STATUS = {
    "attempted_failure": "nonzero",
    "partial_commit": "partial",
    "unchanged": "zero_persistent_writes",
    "cross_store_atomicity": False,
}
_STAGE7_DRY_RUN = {
    "provider": "none",
    "database": "none",
    "lock": "none",
    "state": "none",
    "store_aliases": "sanitized_aliases_only",
}
_STAGE8_EXPORT_ID = "atlas.fixture_snapshot"
_STAGE8_EXPORT_DATASETS = (
    "fixture.market.daily_prices",
    "fixture.macro.gdp_vintages",
    "fixture.company.issuers",
    "fixture.news.items",
)
_STAGE8_EXPORT_PROJECTIONS: tuple[Mapping[str, Any], ...] = (
    {
        "id": "market-prices",
        "operation_id": "market.daily_prices",
        "store": "market",
        "dataset": "fixture.market.daily_prices",
        "relations": ("prices_daily", "prices_daily_versions"),
        "schema_id": "atlas.fixture_snapshot.market_prices.v1",
        "fields": (
            ("instrument_id", "string", False),
            ("trade_date", "date", False),
            ("provider", "string", False),
            ("price_variant", "string", False),
            ("currency_segment", "string", False),
            ("open", "decimal_string", False),
            ("high", "decimal_string", False),
            ("low", "decimal_string", False),
            ("close", "decimal_string", False),
            ("volume", "integer", False),
            ("available_at", "temporal_string", False),
            ("available_precision", "temporal_precision", False),
            ("captured_at", "datetime", False),
            ("captured_precision", "temporal_precision", False),
            ("correction_sequence", "integer", False),
        ),
        "row_identity": (
            "instrument_id",
            "trade_date",
            "provider",
            "price_variant",
            "currency_segment",
        ),
        "order_by": (
            "instrument_id",
            "trade_date",
            "provider",
            "price_variant",
            "currency_segment",
            "correction_sequence",
        ),
        "max_rows": 5000,
    },
    {
        "id": "gdp-vintages",
        "operation_id": "macro.gdp_vintages",
        "store": "macro",
        "dataset": "fixture.macro.gdp_vintages",
        "relations": ("gdp_vintages",),
        "schema_id": "atlas.fixture_snapshot.gdp_vintages.v1",
        "fields": (
            ("source", "string", False),
            ("source_vintage_identity", "string", False),
            ("release_stage", "string", True),
            ("vintage_at", "temporal_string", False),
            ("vintage_precision", "temporal_precision", False),
            ("source_published_at", "temporal_string", True),
            ("source_published_precision", "source_temporal_precision", False),
            ("available_at", "temporal_string", False),
            ("available_precision", "temporal_precision", False),
        ),
        "row_identity": ("source", "source_vintage_identity"),
        "order_by": ("source", "vintage_at", "source_vintage_identity"),
        "max_rows": 1000,
    },
    {
        "id": "company-issuers",
        "operation_id": "company.issuers",
        "store": "company",
        "dataset": "fixture.company.issuers",
        "relations": ("company_issuers", "company_issuer_versions"),
        "schema_id": "atlas.fixture_snapshot.company_issuers.v1",
        "fields": (
            ("issuer_id", "string", False),
            ("cik", "string", False),
            ("legal_name", "string", True),
            ("entity_type", "string", True),
            ("name_state", "string", False),
            ("missing_reason", "string", True),
            ("available_at", "temporal_string", False),
            ("available_precision", "temporal_precision", False),
            ("version_sequence", "integer", False),
        ),
        "row_identity": ("issuer_id",),
        "order_by": ("cik", "issuer_id", "version_sequence"),
        "max_rows": 1000,
    },
    {
        "id": "news-items",
        "operation_id": "news.items",
        "store": "news",
        "dataset": "fixture.news.items",
        "relations": ("news_items", "news_item_versions"),
        "schema_id": "atlas.fixture_snapshot.news_items.v1",
        "fields": (
            ("item_id", "string", False),
            ("source_name", "string", False),
            ("source_item_id", "string", False),
            ("source_kind", "string", False),
            ("headline", "string", True),
            ("published_at", "temporal_string", True),
            ("published_precision", "source_temporal_precision", False),
            ("content_state", "string", False),
            ("content_missing_reason", "string", True),
            ("item_state", "string", False),
            ("retraction_reason", "string", True),
            ("available_at", "temporal_string", False),
            ("available_precision", "temporal_precision", False),
            ("captured_at", "datetime", False),
            ("captured_precision", "temporal_precision", False),
            ("version_sequence", "integer", False),
        ),
        "row_identity": ("item_id",),
        "order_by": ("source_name", "source_item_id", "version_sequence"),
        "max_rows": 5000,
    },
)
_STAGE8_EXPORT_EXCLUDED_FIELDS = (
    "database_path",
    "filesystem_path",
    "credential",
    "sql",
    "raw_artifact",
    "private_receipt",
    "body",
    "summary",
    "source_url",
    "run_id",
    "artifact_id",
    "snapshot_id",
)


def _stage8_expected_export() -> dict[str, Any]:
    """Return the one reviewed, fixture-only Atlas JSON export declaration."""

    projections: list[dict[str, Any]] = []
    schemas: list[dict[str, Any]] = []
    for layout in _STAGE8_EXPORT_PROJECTIONS:
        fields = [
            {"name": name, "type": type_name, "nullable": nullable}
            for name, type_name, nullable in layout["fields"]
        ]
        order_by = [
            {"field": field, "direction": "asc"} for field in layout["order_by"]
        ]
        schema = {
            "id": layout["schema_id"],
            "fields": fields,
            "row_identity": list(layout["row_identity"]),
            "order_by": order_by,
            "constraints": {
                "additional_properties": False,
                "finite_numbers": "reject",
                "row_identity_unique": True,
                "total_order": True,
            },
        }
        schemas.append(schema)
        projections.append(
            {
                "id": layout["id"],
                "operation_id": layout["operation_id"],
                "store": layout["store"],
                "dataset": layout["dataset"],
                "relations": list(layout["relations"]),
                "schema_id": layout["schema_id"],
                "fields": [field["name"] for field in fields],
                "row_identity": list(layout["row_identity"]),
                "order_by": order_by,
                "bounds": {"max_rows": layout["max_rows"]},
            }
        )

    schema_material = {
        "id": "atlas.fixture_snapshot.schema",
        "version": "1.0.0",
        "serialization": {
            "encoding": "utf-8",
            "format": "strict_json",
            "non_finite": "reject",
        },
        "schemas": schemas,
    }
    schema_contract = {
        **schema_material,
        "sha256": hashlib.sha256(
            dumps_strict(schema_material).encode("utf-8")
        ).hexdigest(),
    }
    return {
        "id": _STAGE8_EXPORT_ID,
        "version": "1.0.0",
        "lifecycle": {
            "mode": "manual_only",
            "fixture_only": True,
            "network": False,
            "hosting": False,
            "status": "fixture_validated",
        },
        "owner": "quant_data.atlas",
        "description": "Bounded synthetic-fixture Atlas snapshot for static read-only review.",
        "semantic_dataset_id": "atlas.fixture_snapshot.core_v1",
        "kind": "atlas_snapshot",
        "format": "json",
        "datasets": list(_STAGE8_EXPORT_DATASETS),
        "query_contract": {
            "id": "atlas.fixture_snapshot.query",
            "version": "1.0.0",
            "cutoff": {
                "source": "export_start",
                "precision": "datetime",
                "timezone": "aware_utc",
                "availability": "at_or_before",
                "date_only_policy": "completed_date",
                "vintage_mode": "as_of",
            },
            "projections": projections,
            "bounds": {
                "max_total_rows": 12000,
                "max_bytes": 8 * 1024 * 1024,
                "max_runtime_seconds": 30,
            },
            "excluded_fields": list(_STAGE8_EXPORT_EXCLUDED_FIELDS),
        },
        "schema_contract": schema_contract,
        "chunking": {
            "version": "1.0.0",
            "strategy": "ordered_rows",
            "max_rows": 250,
            "max_bytes": 262144,
            "empty_policy": "omit",
        },
        "freshness": {
            "mode": "derived_from_dataset_freshness",
            "required_datasets": "all",
            "unavailable_state": "explicit",
        },
        "source_mode": "online_backup",
        "consistency": {
            "complete_receipts": "required",
            "receipt_chain": "complete_baseline_plus_validated_deltas",
            "partial_scope": "forbidden",
            "cross_store_atomic": False,
            "source_copies": "online_backup_per_store",
            "read_access": "query_only",
        },
        "staging": {
            "root": "host_selected",
            "child": "unique_generated",
            "revision": "immutable",
            "promotion": "atomic_pointer",
            "cleanup": "exact_child_only",
            "validation": "complete_before_promote",
        },
        "optional_dependency": None,
        "benchmark": {
            "decision": "not_required_json_atlas_snapshot",
            "parquet": "not_adopted",
            "duckdb": "not_adopted",
        },
        "consumers": [
            {
                "id": "quant_data_atlas",
                "mode": "static_read_only",
                "hosting": False,
            }
        ],
        "provenance": {
            "manifest": "required",
            "registry_version": "required",
            "dataset_contract_versions": "required",
            "source_receipts": "required",
            "source_fingerprints": "required",
            "code_version": "required",
            "generated_at": "required",
        },
    }


_STAGE6_DASHBOARD_ROUTES = {
    _STAGE1_DASHBOARD_ID: "/",
    "stage6.gdp_vintages": "/gdp-vintages",
    "stage6.table_inspector": "/table-inspector",
    "stage6.agent_tools": "/agent-tools",
}
_STAGE6_AGENT_DATASET_IDS = frozenset(
    {
        "fixture.company.corporate_actions",
        "fixture.company.expectations",
        "fixture.company.filings",
        "fixture.company.fundamentals",
        "fixture.company.issuers",
        "fixture.macro.economic_calendar",
        "fixture.macro.eia_retail",
        "fixture.macro.eia_weekly",
        "fixture.macro.gdp_vintages",
        "fixture.macro.recession_periods",
        "fixture.macro.rtdsm_employ",
        "fixture.macro.soma_summary",
        "fixture.macro.stage3_catalog",
        "fixture.macro.treasury_yield_curves",
        "fixture.market.controlled_universes",
        "fixture.market.daily_prices",
        "fixture.market.instrument_classifications",
        "fixture.market.instruments",
        "fixture.market.option_capture_evidence",
        "fixture.market.options",
        "fixture.news.items",
        "fixture.news.search_index",
    }
)
_STAGE6_DASHBOARD_EXPECTATIONS = {
    _STAGE1_DASHBOARD_ID: {
        "datasets": frozenset(_STAGE2_DATASET_IDS),
        "tools": STAGE1_TOOL_NAMES,
        "relations": ("prices_daily", "macro_observation_versions"),
        "api_routes": ("/api/health", "/api/price-series"),
        "query_contract_sha256": "8f29f1468c36cf502c50a4ab2f328cfa88f303f229c23f048a0128b2a93a2f1c",
    },
    "stage6.gdp_vintages": {
        "datasets": frozenset(
            {"fixture.macro.gdp_vintages", "fixture.macro.rtdsm_employ"}
        ),
        "tools": ("macro.get_series", "macro.revision_analysis"),
        "relations": ("gdp_vintages", "macro_observation_versions"),
        "api_routes": ("/api/gdp-vintages",),
        "query_contract_sha256": "6b6c3d7841abe94facf070a7a1a5682b7b6b1df4365acf56d80e692db6074b47",
    },
    "stage6.table_inspector": {
        "datasets": frozenset(
            {
                "fixture.market.daily_prices",
                "fixture.macro.rtdsm_employ",
                "fixture.company.filings",
                "fixture.news.items",
            }
        ),
        "tools": (),
        "relations": (
            "prices_daily",
            "macro_observation_versions",
            "company_sec_filings",
            "news_items",
        ),
        "api_routes": ("/api/table-inspector",),
        "query_contract_sha256": "e4cf8b60b983d49ab3304a8536d970914eecf746351828391efe4e204fc4b538",
    },
    "stage6.agent_tools": {
        "datasets": _STAGE6_AGENT_DATASET_IDS,
        "tools": PUBLIC_TOOL_NAMES,
        "relations": (),
        "api_routes": ("/api/agent-tools", "/api/agent-tools/call"),
        "query_contract_sha256": "a3e16295b235b35b6b456eecdaa5ce9856c50ec7c3538e7e9385e591641b8704",
    },
}


@dataclass(frozen=True, slots=True)
class StoreDeclaration:
    id: str
    default_path: str
    path_env: str
    anchor_relation: str
    control_tables: tuple[str, ...]
    migration_order: tuple[str, ...]
    write_coordination: Mapping[str, Any]
    backup: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class MigrationDeclaration:
    id: str
    store: str
    ordinal: int
    resource: str
    sha256: str
    semantic_scope: str
    dependencies: tuple[str, ...]
    reconstruction_state: str


@dataclass(frozen=True, slots=True)
class DatasetDeclaration:
    id: str
    version: str
    store: str
    layer: str
    relations: tuple[str, ...]
    physical: Mapping[str, Any]
    identity: Mapping[str, Any]
    temporal: Mapping[str, Any]
    revision_policy: str
    freshness: Mapping[str, Any]
    quality_contract: Mapping[str, Any]
    collector_ids: tuple[str, ...]
    tool_ids: tuple[str, ...]
    dashboard_ids: tuple[str, ...]
    export_ids: tuple[str, ...]
    active: bool

    @property
    def schema_version(self) -> str:
        """Store-local compatibility name for the dataset contract version."""

        return self.version

    @property
    def identity_sha256(self) -> str:
        material = {
            "id": self.id,
            "store": self.store,
            "layer": self.layer,
            "physical": dict(self.physical),
            "identity": dict(self.identity),
        }
        return hashlib.sha256(dumps_strict(material).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class JobStepDeclaration:
    """Validated manual-only collector step in the frozen Stage 7 catalog."""

    id: str
    version: str
    collector_id: str
    depends_on: tuple[str, ...]
    dependency_policy: str
    read_stores: tuple[str, ...]
    write_stores: tuple[str, ...]
    network_mode: str
    configuration_env: tuple[str, ...]
    timeout_seconds: int
    retry_class: str
    if_new: bool
    identity_version: str


@dataclass(frozen=True, slots=True)
class JobDeclaration:
    """Validated manual-fixture-only job plan; never a scheduler installation."""

    id: str
    version: str
    lifecycle: Mapping[str, Any]
    calendar: Mapping[str, Any]
    steps: tuple[JobStepDeclaration, ...]
    required_stores: tuple[str, ...]
    overlap_policy: str
    timeout_seconds: int
    receipt: Mapping[str, Any]
    exit_code_policy: Mapping[str, Any]
    aggregate_status: Mapping[str, Any]
    success_marker: str
    dry_run: Mapping[str, Any]
    owner: str
    escalation: str


@dataclass(frozen=True, slots=True)
class ExportDeclaration:
    """Immutable reviewed Stage 8 Atlas snapshot contract."""

    id: str
    version: str
    lifecycle: Mapping[str, Any]
    owner: str
    description: str
    semantic_dataset_id: str
    kind: str
    format: str
    datasets: tuple[str, ...]
    source_stores: tuple[str, ...]
    query_contract: Mapping[str, Any]
    schema_contract: Mapping[str, Any]
    chunking: Mapping[str, Any]
    freshness: Mapping[str, Any]
    source_mode: str
    consistency: Mapping[str, Any]
    staging: Mapping[str, Any]
    optional_dependency: str | None
    benchmark: Mapping[str, Any]
    consumers: tuple[Mapping[str, Any], ...]
    provenance: Mapping[str, Any]


def _freeze_job_value(value: Any) -> Any:
    """Detach validated job policy material from mutable registry JSON."""

    if isinstance(value, dict):
        return MappingProxyType(
            {key: _freeze_job_value(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_job_value(item) for item in value)
    return value


def _freeze_job_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    frozen = _freeze_job_value(dict(value))
    if not isinstance(frozen, Mapping):
        raise TypeError("Frozen job policy must remain a mapping")
    return frozen


@dataclass(frozen=True, slots=True)
class Registry:
    schema_id: str
    schema_version: str
    registry_version: str
    status: str
    project_root: Path
    source_path: Path
    stores: tuple[StoreDeclaration, ...]
    migrations: tuple[MigrationDeclaration, ...]
    datasets: tuple[DatasetDeclaration, ...]
    collectors: tuple[Mapping[str, Any], ...]
    jobs: tuple[JobDeclaration, ...]
    exports: tuple[ExportDeclaration, ...]
    tools: tuple[Mapping[str, Any], ...]
    tool_version_policies: tuple[Mapping[str, Any], ...]
    dashboard: tuple[Mapping[str, Any], ...]
    raw: Mapping[str, Any]
    source_sha256: str | None = None

    @property
    def revision(self) -> str:
        """Compatibility accessor used by the validated Stage 1 boundary."""

        return self.registry_version

    @property
    def registry_id(self) -> str:
        return self.schema_id

    def store(self, role: str) -> StoreDeclaration:
        for declaration in self.stores:
            if declaration.id == role:
                return declaration
        raise RegistryError("Unknown store declaration")

    def migrations_for(self, role: str) -> tuple[MigrationDeclaration, ...]:
        return tuple(item for item in self.migrations if item.store == role)

    def datasets_for(self, role: str) -> tuple[DatasetDeclaration, ...]:
        return tuple(item for item in self.datasets if item.store == role)

    def tool(self, name: str, version: str | None = None) -> Mapping[str, Any]:
        for item in self.tools:
            if item["id"] == name:
                if version is None or item["version"] == version:
                    return item
                break
        for policy in self.tool_version_policies:
            if policy["tool"] != name:
                continue
            for variant in policy["variants"]:
                if variant["version"] == version:
                    return variant
            break
        raise RegistryError("Unknown tool declaration")

    def versions_for(self, name: str) -> tuple[Mapping[str, Any], ...]:
        base = self.tool(name)
        for policy in self.tool_version_policies:
            if policy["tool"] == name:
                return (base, *tuple(policy["variants"]))
        return (base,)

    def version_policy(self, name: str) -> Mapping[str, Any] | None:
        for policy in self.tool_version_policies:
            if policy["tool"] == name:
                return policy
        return None

    def job(self, name: str) -> JobDeclaration:
        for item in self.jobs:
            if item.id == name:
                return item
        raise RegistryError("Unknown job declaration")

    def export(self, name: str) -> ExportDeclaration:
        for item in self.exports:
            if item.id == name:
                return item
        raise RegistryError("Unknown export declaration")


def _error(pointer: str, rule: str, message: str) -> RegistryError:
    return RegistryError(message, issues=(Issue(pointer, rule, message),))


def _safe_relative_path(raw: Any, pointer: str) -> str:
    if not isinstance(raw, str) or not raw:
        raise _error(pointer, "path", "Expected a nonempty relative path")
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        raise _error(pointer, "path", "Path must be project-relative without traversal")
    normalized = path.as_posix()
    if normalized != raw or "\\" in raw:
        raise _error(pointer, "path", "Path must use normalized forward-slash form")
    return normalized


def _require_keys(value: Mapping[str, Any], required: set[str], pointer: str) -> None:
    missing = required - set(value)
    if missing:
        raise _error(pointer, "required", f"Missing required fields: {sorted(missing)}")
    unknown = set(value) - required
    if unknown:
        raise _error(
            pointer,
            "additional_properties",
            f"Unknown fields: {sorted(unknown)}",
        )


def _string_array(
    value: Any,
    pointer: str,
    *,
    allow_empty: bool = True,
    identifiers: bool = False,
) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or (not allow_empty and not value)
        or not all(isinstance(item, str) and item for item in value)
        or len(value) != len(set(value))
    ):
        raise _error(pointer, "type", "Expected a unique string array")
    result = tuple(value)
    if identifiers and not all(_IDENTIFIER.fullmatch(item) for item in result):
        raise _error(pointer, "identifier", "Expected safe SQL identifiers")
    return result


def _stable_identifier(value: Any, pointer: str) -> str:
    if not isinstance(value, str) or not _STABLE_IDENTIFIER.fullmatch(value):
        raise _error(
            pointer,
            "identifier",
            "Expected a normalized lowercase stable identifier",
        )
    return value


def _stable_identifier_array(
    value: Any,
    pointer: str,
    *,
    allow_empty: bool = True,
) -> tuple[str, ...]:
    result = _string_array(value, pointer, allow_empty=allow_empty)
    for index, item in enumerate(result):
        _stable_identifier(item, f"{pointer}/{index}")
    return result


def _nonempty_mapping(value: Any, pointer: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or not value:
        raise _error(pointer, "type", "Expected a nonempty object")
    return value


def _validate_strict_schema(schema: Any, pointer: str) -> None:
    if not isinstance(schema, dict):
        raise _error(pointer, "schema", "Schema must be an object")
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        if (
            not schema_type
            or not all(isinstance(item, str) for item in schema_type)
            or not set(schema_type).issubset(
                {"string", "integer", "number", "boolean", "null"}
            )
            or len(schema_type) != len(set(schema_type))
        ):
            raise _error(pointer, "schema", "Unsupported union schema type")
        allowed = {
            "type",
            "enum",
            "minimum",
            "maximum",
            "minLength",
            "maxLength",
        }
        if set(schema) - allowed:
            raise _error(
                pointer,
                "schema",
                "Union schema contains an unsupported keyword",
            )
        nonnull = set(schema_type) - {"null"}
        if set(schema) != {"type"} and (
            "null" not in schema_type or len(nonnull) != 1
        ):
            raise _error(
                pointer,
                "schema",
                "Constrained unions must contain one scalar type and null",
            )
        if "enum" in schema:
            enum = schema["enum"]
            if (
                not isinstance(enum, list)
                or not enum
                or None not in enum
                or any(
                    item is not None
                    and (
                        ("string" in nonnull and not isinstance(item, str))
                        or (
                            "integer" in nonnull
                            and (
                                isinstance(item, bool)
                                or not isinstance(item, int)
                            )
                        )
                    )
                    for item in enum
                )
            ):
                raise _error(
                    pointer,
                    "schema",
                    "Nullable union enum is invalid",
                )
        for bound in ("minimum", "maximum", "minLength", "maxLength"):
            if bound in schema and (
                isinstance(schema[bound], bool)
                or not isinstance(schema[bound], int)
            ):
                raise _error(
                    pointer,
                    "schema",
                    "Nullable union bound must be an integer",
                )
        if schema.get("minimum", 0) > schema.get("maximum", MAX_JSON_BYTES):
            raise _error(pointer, "schema", "Nullable numeric bounds are inverted")
        if schema.get("minLength", 0) > schema.get("maxLength", MAX_JSON_BYTES):
            raise _error(pointer, "schema", "Nullable string bounds are inverted")
        return
    if schema_type == "object":
        allowed = {"type", "additionalProperties", "properties", "required"}
        if set(schema) - allowed:
            raise _error(pointer, "schema", "Object schema contains an unsupported keyword")
        if schema.get("additionalProperties") is not False:
            raise _error(pointer, "strict_schema", "Every object schema must reject extra fields")
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            raise _error(pointer, "schema", "Object schema requires properties")
        required = schema.get("required", [])
        if (
            not isinstance(required, list)
            or not all(isinstance(name, str) for name in required)
            or len(required) != len(set(required))
            or not set(required).issubset(properties)
        ):
            raise _error(pointer, "schema", "Object required fields must be unique properties")
        prohibited = _PROHIBITED_TOOL_KEYS.intersection(properties)
        if prohibited:
            raise _error(pointer, "prohibited", "Tool schema contains a prohibited field")
        for name, child in properties.items():
            _validate_strict_schema(child, f"{pointer}/properties/{name}")
    elif schema_type == "array":
        allowed = {"type", "items", "minItems", "maxItems"}
        if set(schema) - allowed:
            raise _error(pointer, "schema", "Array schema contains an unsupported keyword")
        for bound in ("minItems", "maxItems"):
            if bound in schema and (
                not isinstance(schema[bound], int)
                or isinstance(schema[bound], bool)
                or schema[bound] < 0
            ):
                raise _error(pointer, "schema", "Array bounds must be nonnegative integers")
        if schema.get("minItems", 0) > schema.get("maxItems", MAX_JSON_BYTES):
            raise _error(pointer, "schema", "Array bounds are inverted")
        _validate_strict_schema(schema.get("items"), f"{pointer}/items")
    elif schema_type in {"string", "integer", "number", "boolean", "null"}:
        allowed = {"type", "const", "enum"}
        if schema_type == "string":
            allowed.update({"format", "minLength", "maxLength"})
        if schema_type in {"integer", "number"}:
            allowed.update({"minimum", "maximum"})
        if set(schema) - allowed:
            raise _error(pointer, "schema", "Scalar schema contains an unsupported keyword")
        if "enum" in schema and (
            not isinstance(schema["enum"], list) or not schema["enum"]
        ):
            raise _error(pointer, "schema", "Schema enum must be a nonempty array")
    else:
        raise _error(pointer, "schema", "Unsupported or missing schema type")


def _validate_top_level(raw: Any) -> Mapping[str, Any]:
    if not isinstance(raw, dict):
        raise RegistryError("Registry root must be an object")
    required = {
        "schema_id",
        "schema_version",
        "registry_version",
        "status",
        "compatibility_target",
        "stores",
        "migrations",
        "datasets",
        "collectors",
        "jobs",
        "tools",
        "dashboard",
        "exports",
        "tool_schema_catalog",
        "tool_version_schema_catalog",
        "tool_versions",
        "presentation_order",
    }
    _require_keys(raw, required, "/")
    schema_id = _stable_identifier(raw["schema_id"], "/schema_id")
    if (
        schema_id != "quant_data.system_registry"
        or raw["schema_version"] != "1.9.0"
        or not isinstance(raw["registry_version"], str)
        or not _SEMVER.fullmatch(raw["registry_version"])
        or raw["registry_version"] not in {"2.67.0", "2.68.0", "2.69.0", "2.70.0", "2.71.0", "2.72.0", "2.73.0", "2.74.0", "2.75.0", "2.76.0", "2.77.0", "2.78.0", "2.79.0", "2.80.0", "2.81.0", "2.82.0", "2.83.0", "2.84.0", "2.85.0", "2.86.0", "2.87.0", "2.88.0", "2.89.0", "2.90.0", "2.91.0", "2.92.0", "2.93.0", "2.94.0", "2.95.0", "2.96.0", "2.97.0", "2.98.0"}
        or raw["status"] != "validated"
    ):
        raise RegistryError("Unsupported registry schema, version, or lifecycle status")
    for collection in ("stores", "migrations", "datasets", "collectors", "jobs", "tools", "tool_versions", "dashboard", "exports"):
        if not isinstance(raw[collection], list):
            raise _error(f"/{collection}", "type", "Expected an array")
    return raw



# Cache only fully validated, privately owned schema bundles. Every load still
# resolves the resource and hashes its current bytes before consulting this cache.
# Four entries bound retention across alternate roots/catalog revisions; callers
# receive detached copies, so one Registry cannot mutate another's contracts.
_SCHEMA_CATALOG_CACHE_LIMIT = 4
_SCHEMA_CATALOG_CACHE: _OrderedDict[tuple[Any, ...], dict[str, Mapping[str, Any]]] = _OrderedDict()
_SCHEMA_CATALOG_CACHE_LOCK = _RLock()


def _load_tool_schema_catalog(
    raw: Mapping[str, Any],
    root: Path,
    *,
    declaration_key: str = "tool_schema_catalog",
    expected_id: str = CATALOG_ID,
    expected_version: str = CATALOG_VERSION,
    expected_count: int = 114,
    allowed_tool_names: tuple[str, ...] = PUBLIC_TOOL_NAMES,
) -> dict[str, Mapping[str, Any]]:
    declaration = raw[declaration_key]
    pointer = f"/{declaration_key}"
    if not isinstance(declaration, dict):
        raise _error(pointer, "type", "Tool schema catalog declaration must be an object")
    _require_keys(
        declaration,
        {"schema_id", "schema_version", "resource", "sha256"},
        pointer,
    )
    if (
        declaration["schema_id"] != expected_id
        or declaration["schema_version"] != expected_version
        or not isinstance(declaration["sha256"], str)
        or not _SHA256.fullmatch(declaration["sha256"])
    ):
        raise _error(pointer, "catalog", "Tool schema catalog metadata is invalid")
    resource = _safe_relative_path(declaration["resource"], f"{pointer}/resource")
    resource_path = (root / resource).resolve(strict=True)
    try:
        resource_path.relative_to(root)
    except ValueError as exc:
        raise _error(
            f"{pointer}/resource",
            "containment",
            "Tool schema catalog must remain inside the project",
        ) from exc
    payload = resource_path.read_bytes()
    content_sha256 = hashlib.sha256(payload).hexdigest()
    if content_sha256 != declaration["sha256"]:
        raise _error(
            f"{pointer}/sha256",
            "checksum",
            "Tool schema catalog checksum mismatch",
        )
    # Validation expectations are part of the key: identical content accepted
    # under one catalog contract must never bypass a different contract's checks.
    cache_key = (
        content_sha256, declaration_key, expected_id, expected_version,
        expected_count, tuple(allowed_tool_names), SCHEMA_DIALECT,
    )
    with _SCHEMA_CATALOG_CACHE_LOCK:
        cached = _SCHEMA_CATALOG_CACHE.get(cache_key)
        if cached is not None:
            _SCHEMA_CATALOG_CACHE.move_to_end(cache_key)
    if cached is not None:
        return copy.deepcopy(cached)

    catalog = loads_strict(payload, max_bytes=MAX_JSON_BYTES)
    if not isinstance(catalog, dict):
        raise _error(pointer, "catalog", "Tool schema catalog must be an object")
    _require_keys(
        catalog,
        {"schema_id", "schema_version", "dialect", "contracts"},
        pointer,
    )
    if (
        catalog["schema_id"] != expected_id
        or catalog["schema_version"] != expected_version
        or catalog["dialect"] != SCHEMA_DIALECT
        or not isinstance(catalog["contracts"], list)
        or len(catalog["contracts"]) != expected_count
    ):
        raise _error(pointer, "catalog", "Tool schema catalog identity or size drifted")
    contracts: dict[str, Mapping[str, Any]] = {}
    for index, contract in enumerate(catalog["contracts"]):
        contract_pointer = f"{pointer}/contracts/{index}"
        if not isinstance(contract, dict):
            raise _error(contract_pointer, "type", "Schema contract must be an object")
        _require_keys(contract, {"id", "tool", "direction", "schema"}, contract_pointer)
        if (
            not isinstance(contract["id"], str)
            or contract["id"] in contracts
            or contract["tool"] not in allowed_tool_names
            or contract["direction"] not in {"input", "output"}
            or not isinstance(contract["schema"], dict)
        ):
            raise _error(contract_pointer, "catalog", "Schema contract metadata is invalid")
        schema = copy.deepcopy(contract["schema"])
        if (
            schema.pop("$schema", None) != SCHEMA_DIALECT
            or schema.pop("$id", None) != contract["id"]
        ):
            raise _error(contract_pointer, "catalog", "Generated schema identity drifted")
        _validate_strict_schema(schema, f"{contract_pointer}/schema")
        contracts[contract["id"]] = schema
    with _SCHEMA_CATALOG_CACHE_LOCK:
        _SCHEMA_CATALOG_CACHE[cache_key] = contracts
        _SCHEMA_CATALOG_CACHE.move_to_end(cache_key)
        while len(_SCHEMA_CATALOG_CACHE) > _SCHEMA_CATALOG_CACHE_LIMIT:
            _SCHEMA_CATALOG_CACHE.popitem(last=False)
    return copy.deepcopy(contracts)



def _derived_collector_stores(
    collector: Mapping[str, Any],
    dataset_store_by_id: Mapping[str, str],
    field: str,
) -> tuple[str, ...]:
    """Return stable first-seen store ownership for one collector field."""

    return tuple(
        dict.fromkeys(dataset_store_by_id[dataset_id] for dataset_id in collector[field])
    )


def _validate_stage7_jobs(
    raw_jobs: Any,
    *,
    collectors: tuple[Mapping[str, Any], ...],
    dataset_store_by_id: Mapping[str, str],
    expected_roles: set[str],
) -> tuple[JobDeclaration, ...]:
    """Validate the frozen, manual-fixture-only Stage 7 orchestration catalog."""

    if not isinstance(raw_jobs, list):
        raise _error("/jobs", "type", "Stage 7 jobs must be an array")

    collector_by_id = {str(item["id"]): item for item in collectors}
    required_job_keys = {
        "id",
        "version",
        "lifecycle",
        "calendar",
        "steps",
        "required_stores",
        "overlap_policy",
        "timeout_seconds",
        "receipt",
        "exit_code_policy",
        "aggregate_status",
        "success_marker",
        "dry_run",
        "owner",
        "escalation",
    }
    required_step_keys = {
        "id",
        "version",
        "collector_id",
        "depends_on",
        "dependency_policy",
        "read_stores",
        "write_stores",
        "network_mode",
        "configuration_env",
        "timeout_seconds",
        "retry_class",
        "if_new",
        "identity_version",
    }
    jobs: list[JobDeclaration] = []
    job_ids: list[str] = []

    for index, value in enumerate(raw_jobs):
        pointer = f"/jobs/{index}"
        if not isinstance(value, dict):
            raise _error(pointer, "type", "Job declaration must be an object")
        _require_keys(value, required_job_keys, pointer)
        job_id = _stable_identifier(value["id"], f"{pointer}/id")
        layout = _STAGE7_JOB_LAYOUTS.get(job_id)
        if layout is None or job_id in job_ids:
            raise _error(f"{pointer}/id", "job", "Job is not in the frozen Stage 7 catalog")
        if (
            value["version"] != "1.0.0"
            or not isinstance(value["version"], str)
            or not _SEMVER.fullmatch(value["version"])
            or value["overlap_policy"] != "ignore_new"
            or value["owner"] != "quant_data.operations"
            or value["escalation"] != "manual_review"
            or value["required_stores"] != "derived_from_steps"
        ):
            raise _error(pointer, "job", "Stage 7 job metadata drifted")

        lifecycle = _nonempty_mapping(value["lifecycle"], f"{pointer}/lifecycle")
        _require_keys(lifecycle, set(_STAGE7_LIFECYCLE), f"{pointer}/lifecycle")
        if lifecycle != _STAGE7_LIFECYCLE:
            raise _error(f"{pointer}/lifecycle", "lifecycle", "Stage 7 lifecycle drifted")

        calendar = _nonempty_mapping(value["calendar"], f"{pointer}/calendar")
        expected_calendar = {
            "mode": "manual_fixture_only",
            "recovered_cadence": layout["recovered_cadence"],
            "timezone_policy": "unreconciled",
            "external_definition": "unresolved",
        }
        _require_keys(calendar, set(expected_calendar), f"{pointer}/calendar")
        if calendar != expected_calendar:
            raise _error(f"{pointer}/calendar", "calendar", "Stage 7 calendar drifted")

        receipt = _nonempty_mapping(value["receipt"], f"{pointer}/receipt")
        _require_keys(receipt, set(_STAGE7_RECEIPT), f"{pointer}/receipt")
        if receipt != _STAGE7_RECEIPT:
            raise _error(f"{pointer}/receipt", "receipt", "Stage 7 receipt contract drifted")

        exit_code_policy = _nonempty_mapping(
            value["exit_code_policy"], f"{pointer}/exit_code_policy"
        )
        _require_keys(
            exit_code_policy,
            set(_STAGE7_EXIT_CODE_POLICY),
            f"{pointer}/exit_code_policy",
        )
        mapping = exit_code_policy["mapping"]
        if not isinstance(mapping, dict):
            raise _error(
                f"{pointer}/exit_code_policy/mapping",
                "type",
                "Exit-code mapping must be an object",
            )
        _require_keys(
            mapping,
            set(_STAGE7_EXIT_CODE_POLICY["mapping"]),
            f"{pointer}/exit_code_policy/mapping",
        )
        if exit_code_policy != _STAGE7_EXIT_CODE_POLICY:
            raise _error(
                f"{pointer}/exit_code_policy",
                "exit_code_policy",
                "Stage 7 exit-code policy drifted",
            )

        aggregate_status = _nonempty_mapping(
            value["aggregate_status"], f"{pointer}/aggregate_status"
        )
        _require_keys(
            aggregate_status,
            set(_STAGE7_AGGREGATE_STATUS),
            f"{pointer}/aggregate_status",
        )
        if aggregate_status != _STAGE7_AGGREGATE_STATUS:
            raise _error(
                f"{pointer}/aggregate_status",
                "aggregate_status",
                "Stage 7 aggregate status drifted",
            )

        dry_run = _nonempty_mapping(value["dry_run"], f"{pointer}/dry_run")
        _require_keys(dry_run, set(_STAGE7_DRY_RUN), f"{pointer}/dry_run")
        if dry_run != _STAGE7_DRY_RUN:
            raise _error(f"{pointer}/dry_run", "dry_run", "Stage 7 dry-run contract drifted")

        expected_collectors = tuple(layout["collectors"])
        raw_steps = value["steps"]
        if not isinstance(raw_steps, list) or len(raw_steps) != len(expected_collectors):
            raise _error(f"{pointer}/steps", "steps", "Stage 7 step graph is incomplete")
        steps: list[JobStepDeclaration] = []
        prior_step_ids: set[str] = set()
        for step_index, raw_step in enumerate(raw_steps):
            step_pointer = f"{pointer}/steps/{step_index}"
            if not isinstance(raw_step, dict):
                raise _error(step_pointer, "type", "Job step must be an object")
            _require_keys(raw_step, required_step_keys, step_pointer)
            step_id = _stable_identifier(raw_step["id"], f"{step_pointer}/id")
            collector_id = _stable_identifier(
                raw_step["collector_id"], f"{step_pointer}/collector_id"
            )
            expected_collector_id = expected_collectors[step_index]
            collector = collector_by_id.get(collector_id)
            if (
                collector is None
                or step_id != expected_collector_id
                or collector_id != expected_collector_id
                or collector["network"] is not False
            ):
                raise _error(step_pointer, "collector", "Job step collector is invalid")
            depends_on = _stable_identifier_array(
                raw_step["depends_on"], f"{step_pointer}/depends_on"
            )
            expected_dependencies = (
                (expected_collectors[0],)
                if job_id == "options-close" and step_index == 1
                else ()
            )
            if (
                depends_on != expected_dependencies
                or not set(depends_on).issubset(prior_step_ids)
                or raw_step["dependency_policy"]
                != ("required_predecessor" if depends_on else "independent")
            ):
                raise _error(
                    f"{step_pointer}/depends_on",
                    "dependency",
                    "Stage 7 step dependencies are invalid or unordered",
                )

            read_stores = _stable_identifier_array(
                raw_step["read_stores"], f"{step_pointer}/read_stores", allow_empty=False
            )
            write_stores = _stable_identifier_array(
                raw_step["write_stores"], f"{step_pointer}/write_stores", allow_empty=False
            )
            expected_write_stores = _derived_collector_stores(
                collector, dataset_store_by_id, "output_datasets"
            )
            expected_read_stores = tuple(
                dict.fromkeys(
                    (
                        *_derived_collector_stores(
                            collector, dataset_store_by_id, "input_datasets"
                        ),
                        *expected_write_stores,
                    )
                )
            )
            if (
                not set(read_stores).issubset(expected_roles)
                or not set(write_stores).issubset(expected_roles)
                or not set(write_stores).issubset(read_stores)
                or write_stores != expected_write_stores
                or read_stores != expected_read_stores
            ):
                raise _error(
                    step_pointer,
                    "stores",
                    "Job step stores must derive exactly from the collector",
                )

            configuration_env = _string_array(
                raw_step["configuration_env"],
                f"{step_pointer}/configuration_env",
            )
            timeout_seconds = raw_step["timeout_seconds"]
            if (
                raw_step["version"] != collector["version"]
                or raw_step["network_mode"] != "fixture_only_no_network"
                or configuration_env != tuple(collector["configuration_env"])
                or isinstance(timeout_seconds, bool)
                or not isinstance(timeout_seconds, int)
                or not 1 <= timeout_seconds <= 300
                or timeout_seconds != collector["workload_bounds"]["max_seconds"]
                or raw_step["retry_class"] != "collector_declared"
                or raw_step["if_new"] is not True
                or raw_step["identity_version"] != "collector_semantic_identity_v1"
            ):
                raise _error(step_pointer, "step", "Stage 7 step policy drifted")
            steps.append(
                JobStepDeclaration(
                    id=step_id,
                    version=raw_step["version"],
                    collector_id=collector_id,
                    depends_on=depends_on,
                    dependency_policy=raw_step["dependency_policy"],
                    read_stores=read_stores,
                    write_stores=write_stores,
                    network_mode=raw_step["network_mode"],
                    configuration_env=configuration_env,
                    timeout_seconds=timeout_seconds,
                    retry_class=raw_step["retry_class"],
                    if_new=raw_step["if_new"],
                    identity_version=raw_step["identity_version"],
                )
            )
            prior_step_ids.add(step_id)

        required_stores = tuple(
            dict.fromkeys(store for step in steps for store in step.write_stores)
        )
        timeout_seconds = value["timeout_seconds"]
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int)
            or timeout_seconds != layout["timeout_seconds"]
            or timeout_seconds < sum(step.timeout_seconds for step in steps)
            or timeout_seconds > 300
            or value["success_marker"] != layout["success_marker"]
        ):
            raise _error(pointer, "timeout", "Stage 7 job timeout or marker drifted")
        jobs.append(
            JobDeclaration(
                id=job_id,
                version=value["version"],
                lifecycle=_freeze_job_mapping(lifecycle),
                calendar=_freeze_job_mapping(calendar),
                steps=tuple(steps),
                required_stores=required_stores,
                overlap_policy=value["overlap_policy"],
                timeout_seconds=timeout_seconds,
                receipt=_freeze_job_mapping(receipt),
                exit_code_policy=_freeze_job_mapping(exit_code_policy),
                aggregate_status=_freeze_job_mapping(aggregate_status),
                success_marker=value["success_marker"],
                dry_run=_freeze_job_mapping(dry_run),
                owner=value["owner"],
                escalation=value["escalation"],
            )
        )
        job_ids.append(job_id)

    if tuple(job_ids) != _STAGE7_JOB_IDS:
        raise RegistryError("Canonical Stage 7 registry must expose eight ordered jobs")
    return tuple(jobs)


def _validate_stage8_exports(
    raw_exports: Any,
    *,
    datasets: tuple[DatasetDeclaration, ...],
    owned_relations: Mapping[tuple[str, str], str],
) -> tuple[ExportDeclaration, ...]:
    """Validate the one closed, fixture-only Stage 8 Atlas export inventory."""

    expected = _stage8_expected_export()
    pointer = "/exports"
    if not isinstance(raw_exports, list) or len(raw_exports) != 1:
        raise _error(pointer, "exports", "Canonical Stage 8 registry requires one export")
    value = raw_exports[0]
    item_pointer = f"{pointer}/0"
    if not isinstance(value, dict):
        raise _error(item_pointer, "type", "Export declaration must be an object")
    _require_keys(value, set(expected), item_pointer)

    export_id = _stable_identifier(value["id"], f"{item_pointer}/id")
    if (
        export_id != _STAGE8_EXPORT_ID
        or value["version"] != "1.0.0"
        or not isinstance(value["version"], str)
        or not _SEMVER.fullmatch(value["version"])
        or not isinstance(value["owner"], str)
        or not _HANDLER.fullmatch(value["owner"])
        or not isinstance(value["description"], str)
        or not value["description"]
        or _stable_identifier(
            value["semantic_dataset_id"], f"{item_pointer}/semantic_dataset_id"
        )
        != "atlas.fixture_snapshot.core_v1"
        or value["kind"] != "atlas_snapshot"
        or value["format"] != "json"
        or value["source_mode"] != "online_backup"
        or value["optional_dependency"] is not None
    ):
        raise _error(item_pointer, "export", "Stage 8 export metadata drifted")

    lifecycle = _nonempty_mapping(value["lifecycle"], f"{item_pointer}/lifecycle")
    _require_keys(lifecycle, set(expected["lifecycle"]), f"{item_pointer}/lifecycle")
    if lifecycle != expected["lifecycle"]:
        raise _error(
            f"{item_pointer}/lifecycle",
            "lifecycle",
            "Stage 8 export must remain manual, fixture-only, offline, and unhosted",
        )

    dataset_ids = _stable_identifier_array(
        value["datasets"], f"{item_pointer}/datasets", allow_empty=False
    )
    if dataset_ids != _STAGE8_EXPORT_DATASETS:
        raise _error(
            f"{item_pointer}/datasets",
            "datasets",
            "Stage 8 export datasets must retain the reviewed ownership order",
        )
    dataset_by_id = {item.id: item for item in datasets}
    if not set(dataset_ids).issubset(dataset_by_id):
        raise _error(
            f"{item_pointer}/datasets",
            "reference",
            "Stage 8 export references an unknown dataset",
        )
    source_stores = tuple(
        dict.fromkeys(dataset_by_id[dataset_id].store for dataset_id in dataset_ids)
    )
    if source_stores != ("market", "macro", "company", "news"):
        raise _error(
            f"{item_pointer}/datasets",
            "stores",
            "Stage 8 export must retain four ordered source stores",
        )

    query = _nonempty_mapping(value["query_contract"], f"{item_pointer}/query_contract")
    expected_query = expected["query_contract"]
    _require_keys(query, set(expected_query), f"{item_pointer}/query_contract")
    if (
        query["id"] != expected_query["id"]
        or query["version"] != expected_query["version"]
        or not isinstance(query["version"], str)
        or not _SEMVER.fullmatch(query["version"])
    ):
        raise _error(
            f"{item_pointer}/query_contract",
            "query_contract",
            "Stage 8 query contract identity drifted",
        )
    cutoff = _nonempty_mapping(
        query["cutoff"], f"{item_pointer}/query_contract/cutoff"
    )
    _require_keys(
        cutoff,
        set(expected_query["cutoff"]),
        f"{item_pointer}/query_contract/cutoff",
    )
    if cutoff != expected_query["cutoff"]:
        raise _error(
            f"{item_pointer}/query_contract/cutoff",
            "cutoff",
            "Stage 8 cutoff must be export-start, aware datetime, and completed-date",
        )
    bounds = _nonempty_mapping(
        query["bounds"], f"{item_pointer}/query_contract/bounds"
    )
    _require_keys(
        bounds,
        set(expected_query["bounds"]),
        f"{item_pointer}/query_contract/bounds",
    )
    if bounds != expected_query["bounds"]:
        raise _error(
            f"{item_pointer}/query_contract/bounds",
            "bounds",
            "Stage 8 export bounds drifted",
        )
    excluded_fields = _string_array(
        query["excluded_fields"],
        f"{item_pointer}/query_contract/excluded_fields",
        allow_empty=False,
        identifiers=True,
    )
    if excluded_fields != _STAGE8_EXPORT_EXCLUDED_FIELDS:
        raise _error(
            f"{item_pointer}/query_contract/excluded_fields",
            "prohibited",
            "Stage 8 export must explicitly exclude unsafe/private fields",
        )

    raw_projections = query["projections"]
    if not isinstance(raw_projections, list) or len(raw_projections) != len(
        _STAGE8_EXPORT_PROJECTIONS
    ):
        raise _error(
            f"{item_pointer}/query_contract/projections",
            "projections",
            "Stage 8 export projection inventory is incomplete",
        )
    expected_schemas = {
        str(item["id"]): item for item in expected["schema_contract"]["schemas"]
    }
    seen_projection_ids: set[str] = set()
    for index, (projection, layout) in enumerate(
        zip(raw_projections, _STAGE8_EXPORT_PROJECTIONS)
    ):
        projection_pointer = f"{item_pointer}/query_contract/projections/{index}"
        if not isinstance(projection, dict):
            raise _error(projection_pointer, "type", "Export projection must be an object")
        expected_projection = expected_query["projections"][index]
        _require_keys(projection, set(expected_projection), projection_pointer)
        projection_id = _stable_identifier(projection["id"], f"{projection_pointer}/id")
        operation_id = _stable_identifier(
            projection["operation_id"], f"{projection_pointer}/operation_id"
        )
        if (
            projection_id in seen_projection_ids
            or projection_id != layout["id"]
            or operation_id != layout["operation_id"]
            or projection["store"] != layout["store"]
            or projection["dataset"] != layout["dataset"]
            or projection["dataset"] not in dataset_by_id
            or projection["schema_id"] != layout["schema_id"]
        ):
            raise _error(
                projection_pointer,
                "projection",
                "Stage 8 projection identity, owner, or schema drifted",
            )
        seen_projection_ids.add(projection_id)
        relations = _string_array(
            projection["relations"],
            f"{projection_pointer}/relations",
            allow_empty=False,
            identifiers=True,
        )
        if relations != tuple(layout["relations"]):
            raise _error(
                f"{projection_pointer}/relations",
                "relations",
                "Stage 8 projection relations drifted",
            )
        if any(
            owned_relations.get((projection["store"], relation))
            != projection["dataset"]
            for relation in relations
        ):
            raise _error(
                f"{projection_pointer}/relations",
                "ownership",
                "Stage 8 projection relation is not owned by its declared dataset",
            )
        fields = _string_array(
            projection["fields"],
            f"{projection_pointer}/fields",
            allow_empty=False,
            identifiers=True,
        )
        expected_fields = tuple(name for name, _type, _nullable in layout["fields"])
        if fields != expected_fields or set(fields).intersection(excluded_fields):
            raise _error(
                f"{projection_pointer}/fields",
                "fields",
                "Stage 8 projection fields must remain public and ordered",
            )
        row_identity = _string_array(
            projection["row_identity"],
            f"{projection_pointer}/row_identity",
            allow_empty=False,
            identifiers=True,
        )
        if (
            row_identity != tuple(layout["row_identity"])
            or not set(row_identity).issubset(fields)
        ):
            raise _error(
                f"{projection_pointer}/row_identity",
                "identity",
                "Stage 8 projection row identity drifted",
            )
        order_by = projection["order_by"]
        if (
            not isinstance(order_by, list)
            or order_by != expected_projection["order_by"]
            or any(
                not isinstance(item, dict)
                or set(item) != {"field", "direction"}
                or item["field"] not in fields
                or item["direction"] != "asc"
                for item in order_by
            )
        ):
            raise _error(
                f"{projection_pointer}/order_by",
                "order",
                "Stage 8 projection must retain a fixed ascending total order",
            )
        projection_bounds = _nonempty_mapping(
            projection["bounds"], f"{projection_pointer}/bounds"
        )
        _require_keys(
            projection_bounds,
            {"max_rows"},
            f"{projection_pointer}/bounds",
        )
        if (
            projection_bounds != {"max_rows": layout["max_rows"]}
            or isinstance(projection_bounds["max_rows"], bool)
            or not isinstance(projection_bounds["max_rows"], int)
        ):
            raise _error(
                f"{projection_pointer}/bounds",
                "bounds",
                "Stage 8 projection row bound drifted",
            )
        schema = expected_schemas.get(str(projection["schema_id"]))
        if (
            schema is None
            or fields != tuple(item["name"] for item in schema["fields"])
            or row_identity != tuple(schema["row_identity"])
            or order_by != schema["order_by"]
        ):
            raise _error(
                projection_pointer,
                "schema_reference",
                "Stage 8 projection does not reconcile to its typed schema",
            )

    schema_contract = _nonempty_mapping(
        value["schema_contract"], f"{item_pointer}/schema_contract"
    )
    expected_schema_contract = expected["schema_contract"]
    _require_keys(
        schema_contract,
        set(expected_schema_contract),
        f"{item_pointer}/schema_contract",
    )
    schema_digest_material = {
        "id": schema_contract["id"],
        "version": schema_contract["version"],
        "serialization": schema_contract["serialization"],
        "schemas": schema_contract["schemas"],
    }
    schema_sha256 = hashlib.sha256(
        dumps_strict(schema_digest_material).encode("utf-8")
    ).hexdigest()
    if (
        schema_contract["sha256"] != schema_sha256
        or schema_contract != expected_schema_contract
    ):
        raise _error(
            f"{item_pointer}/schema_contract",
            "schema_digest",
            "Stage 8 typed schema contract or digest drifted",
        )

    for field_name in (
        "chunking",
        "freshness",
        "consistency",
        "staging",
        "benchmark",
        "provenance",
    ):
        declared = _nonempty_mapping(value[field_name], f"{item_pointer}/{field_name}")
        expected_value = expected[field_name]
        _require_keys(declared, set(expected_value), f"{item_pointer}/{field_name}")
        if declared != expected_value:
            raise _error(
                f"{item_pointer}/{field_name}",
                "export_contract",
                "Stage 8 export lifecycle contract drifted",
            )
    consumers = value["consumers"]
    if (
        not isinstance(consumers, list)
        or consumers != expected["consumers"]
        or len(consumers) != 1
        or not isinstance(consumers[0], dict)
        or set(consumers[0]) != {"id", "mode", "hosting"}
        or consumers[0]["id"] != "quant_data_atlas"
        or consumers[0]["mode"] != "static_read_only"
        or consumers[0]["hosting"] is not False
    ):
        raise _error(
            f"{item_pointer}/consumers",
            "consumer",
            "Stage 8 export has an unsafe or unreviewed consumer",
        )

    if value != expected:
        raise _error(
            item_pointer,
            "reviewed_inventory",
            "Canonical Stage 8 export declaration drifted from the reviewed inventory",
        )

    return (
        ExportDeclaration(
            id=export_id,
            version=value["version"],
            lifecycle=_freeze_job_mapping(lifecycle),
            owner=value["owner"],
            description=value["description"],
            semantic_dataset_id=value["semantic_dataset_id"],
            kind=value["kind"],
            format=value["format"],
            datasets=dataset_ids,
            source_stores=source_stores,
            query_contract=_freeze_job_mapping(query),
            schema_contract=_freeze_job_mapping(schema_contract),
            chunking=_freeze_job_mapping(value["chunking"]),
            freshness=_freeze_job_mapping(value["freshness"]),
            source_mode=value["source_mode"],
            consistency=_freeze_job_mapping(value["consistency"]),
            staging=_freeze_job_mapping(value["staging"]),
            optional_dependency=value["optional_dependency"],
            benchmark=_freeze_job_mapping(value["benchmark"]),
            consumers=tuple(_freeze_job_mapping(item) for item in consumers),
            provenance=_freeze_job_mapping(value["provenance"]),
        ),
    )


def load_registry(
    path: str | Path,
    *,
    project_root: str | Path,
    environment: Mapping[str, str] | None = None,
) -> Registry:
    """Load a registry from an explicitly supplied path.

    The environment mapping is explicit so tests never consult ambient process
    state.  Its optional registry override is honored only when ``path`` is the
    canonical relative path.
    """

    root = Path(project_root).resolve(strict=True)
    supplied = Path(path)
    env = dict(environment or {})
    if supplied == CANONICAL_REGISTRY_PATH and REGISTRY_ENV in env:
        override = env[REGISTRY_ENV]
        if not override:
            raise RegistryError("Registry path override cannot be empty")
        supplied = Path(override)
    source = supplied if supplied.is_absolute() else root / supplied
    source = source.resolve(strict=True)
    try:
        payload = source.read_bytes()
    except OSError as exc:
        raise RegistryError("Registry file is unavailable") from exc
    raw = _validate_top_level(loads_strict(payload, max_bytes=_MAX_REGISTRY_BYTES))
    schema_contracts = _load_tool_schema_catalog(raw, root)
    versioned_schema_contracts = _load_tool_schema_catalog(
        raw,
        root,
        declaration_key="tool_version_schema_catalog",
        expected_id=VERSIONED_CATALOG_ID,
        expected_version=VERSIONED_CATALOG_VERSION,
        expected_count=288,
        allowed_tool_names=CURRENT_PUBLIC_TOOL_NAMES,
    )

    stores: list[StoreDeclaration] = []
    expected_roles = {role.value for role in STORE_ROLES}
    expected_defaults = {
        "market": "data/market.sqlite",
        "macro": "data/macro.sqlite",
        "company": "data/company.sqlite",
        "news": "data/news.sqlite",
    }
    expected_control = (
        "schema_migrations",
        "dataset_registry",
        "dataset_identity_contracts",
        "ingestion_runs",
        "ingestion_run_outputs",
        "ingestion_run_failures",
        "ingestion_artifacts",
        "ingestion_snapshots",
        "ingestion_snapshot_artifacts",
        "data_quality_results",
    )
    seen_roles: set[str] = set()
    for index, value in enumerate(raw["stores"]):
        pointer = f"/stores/{index}"
        if not isinstance(value, dict):
            raise _error(pointer, "type", "Store declaration must be an object")
        _require_keys(
            value,
            {
                "id",
                "default_path",
                "path_env",
                "anchor_relation",
                "control_tables",
                "migration_order",
                "write_coordination",
                "backup",
            },
            pointer,
        )
        role = value["id"]
        if not isinstance(role, str) or role not in expected_roles or role in seen_roles:
            raise _error(f"{pointer}/id", "store", "Store IDs must be the four unique roles")
        seen_roles.add(role)
        default_path = _safe_relative_path(value["default_path"], f"{pointer}/default_path")
        if default_path != expected_defaults[role]:
            raise _error(
                f"{pointer}/default_path",
                "const",
                "Store default path is not the accepted four-store path",
            )
        expected_env = f"QUANT_{role.upper()}_DB_PATH"
        if value["path_env"] != expected_env:
            raise _error(f"{pointer}/path_env", "environment", "Store path override is bound to the wrong role")
        if value["anchor_relation"] != "store_metadata":
            raise _error(f"{pointer}/anchor_relation", "const", "Unexpected store role anchor")
        control = _string_array(
            value["control_tables"],
            f"{pointer}/control_tables",
            allow_empty=False,
            identifiers=True,
        )
        if control != expected_control:
            raise _error(f"{pointer}/control_tables", "control_tables", "Unexpected control table set")
        migration_order = _stable_identifier_array(
            value["migration_order"], f"{pointer}/migration_order"
        )
        coordination = _nonempty_mapping(
            value["write_coordination"], f"{pointer}/write_coordination"
        )
        _require_keys(
            coordination,
            {"scope", "lock_key", "multi_store_order", "timeout_seconds"},
            f"{pointer}/write_coordination",
        )
        if (
            coordination["scope"] != "physical_store"
            or coordination["lock_key"] != "sha256_canonical_path_uri_v1"
            or coordination["multi_store_order"] != "canonical_path_uri"
            or isinstance(coordination["timeout_seconds"], bool)
            or not isinstance(coordination["timeout_seconds"], int)
            or not 1 <= coordination["timeout_seconds"] <= 300
        ):
            raise _error(
                f"{pointer}/write_coordination",
                "coordination",
                "Store write coordination is not the accepted physical-lock contract",
            )
        backup = _nonempty_mapping(value["backup"], f"{pointer}/backup")
        _require_keys(
            backup,
            {"method", "requires_explicit_target", "verify"},
            f"{pointer}/backup",
        )
        if (
            backup["method"] != "sqlite_online_backup"
            or backup["requires_explicit_target"] is not True
            or backup["verify"]
            != [
                "integrity_check",
                "foreign_key_check",
                "migration_ledger",
                "dataset_registry",
            ]
        ):
            raise _error(f"{pointer}/backup", "backup", "Unsupported backup contract")
        stores.append(
            StoreDeclaration(
                role,
                default_path,
                value["path_env"],
                value["anchor_relation"],
                control,
                migration_order,
                coordination,
                backup,
            )
        )
    if seen_roles != expected_roles:
        raise RegistryError("Registry must declare exactly four stores")

    migrations: list[MigrationDeclaration] = []
    migration_ids: set[str] = set()
    migration_resources: set[tuple[str, str]] = set()
    by_store: dict[str, list[MigrationDeclaration]] = {role: [] for role in expected_roles}
    for index, value in enumerate(raw["migrations"]):
        pointer = f"/migrations/{index}"
        if not isinstance(value, dict):
            raise _error(pointer, "type", "Migration declaration must be an object")
        _require_keys(value, {"id", "store", "ordinal", "resource", "sha256", "semantic_scope", "dependencies", "reconstruction_state"}, pointer)
        migration_id = _stable_identifier(value["id"], f"{pointer}/id")
        if (
            migration_id in migration_ids
            or not isinstance(value["store"], str)
            or value["store"] not in expected_roles
        ):
            raise _error(f"{pointer}/id", "unique", "Migration ID/store is invalid")
        if (
            not isinstance(value["ordinal"], int)
            or isinstance(value["ordinal"], bool)
            or value["ordinal"] < 1
        ):
            raise _error(f"{pointer}/ordinal", "type", "Migration ordinal must be positive")
        if not isinstance(value["sha256"], str) or not _SHA256.fullmatch(value["sha256"]):
            raise _error(f"{pointer}/sha256", "format", "Migration checksum must be lowercase SHA-256")
        if not isinstance(value["semantic_scope"], str) or not value["semantic_scope"]:
            raise _error(f"{pointer}/semantic_scope", "type", "Migration scope must be nonempty")
        dependencies = _stable_identifier_array(
            value["dependencies"], f"{pointer}/dependencies"
        )
        migration_ids.add(migration_id)
        resource = _safe_relative_path(value["resource"], f"{pointer}/resource")
        resource_key = (value["store"], resource)
        if resource_key in migration_resources:
            raise _error(
                f"{pointer}/resource",
                "unique",
                "Migration resources must be unique within a store",
            )
        migration_resources.add(resource_key)
        resource_path = (root / resource).resolve(strict=True)
        try:
            resource_path.relative_to(root)
        except ValueError as exc:
            raise _error(f"{pointer}/resource", "containment", "Migration must remain inside the project") from exc
        digest = hashlib.sha256(resource_path.read_bytes()).hexdigest()
        if digest != value["sha256"]:
            raise _error(f"{pointer}/sha256", "checksum", "Migration resource checksum mismatch")
        if value["reconstruction_state"] not in {"unresolved", "fixture_validated", "recovered_exact"}:
            raise _error(f"{pointer}/reconstruction_state", "enum", "Invalid reconstruction state")
        declaration = MigrationDeclaration(
            migration_id,
            value["store"],
            value["ordinal"],
            resource,
            value["sha256"],
            value["semantic_scope"],
            dependencies,
            value["reconstruction_state"],
        )
        migrations.append(declaration)
        by_store[declaration.store].append(declaration)
    for store in stores:
        ordered = sorted(by_store[store.id], key=lambda item: item.ordinal)
        if [item.ordinal for item in ordered] != list(range(1, len(ordered) + 1)):
            raise RegistryError("Migration ordinals must be total and start at one")
        if tuple(item.id for item in ordered) != store.migration_order:
            raise RegistryError("Store migration order does not match migration declarations")
        seen: set[str] = set()
        for item in ordered:
            if not set(item.dependencies).issubset(seen):
                raise RegistryError("Migration dependencies must refer to earlier same-store resources")
            seen.add(item.id)

    datasets: list[DatasetDeclaration] = []
    dataset_ids: set[str] = set()
    owned_relations: dict[tuple[str, str], str] = {}
    for index, value in enumerate(raw["datasets"]):
        pointer = f"/datasets/{index}"
        if not isinstance(value, dict):
            raise _error(pointer, "type", "Dataset declaration must be an object")
        _require_keys(
            value,
            {
                "id",
                "version",
                "store",
                "layer",
                "physical",
                "identity",
                "temporal",
                "revision_policy",
                "freshness",
                "quality_contract",
                "collector_ids",
                "tool_ids",
                "dashboard_ids",
                "export_ids",
                "active",
            },
            pointer,
        )
        dataset_id = _stable_identifier(value["id"], f"{pointer}/id")
        if (
            dataset_id in dataset_ids
            or not isinstance(value["store"], str)
            or value["store"] not in expected_roles
        ):
            raise _error(f"{pointer}/id", "unique", "Dataset ID/store is invalid")
        if not isinstance(value["layer"], str) or value["layer"] not in {
            "evidence",
            "canonical",
            "derived",
        }:
            raise _error(f"{pointer}/layer", "enum", "Invalid dataset layer")
        dataset_ids.add(dataset_id)
        if (
            not isinstance(value["version"], str)
            or not _SEMVER.fullmatch(value["version"])
            or not isinstance(value["active"], bool)
        ):
            raise _error(pointer, "dataset", "Dataset lifecycle/version is invalid")

        physical = _nonempty_mapping(value["physical"], f"{pointer}/physical")
        _require_keys(physical, {"relations"}, f"{pointer}/physical")
        if not isinstance(physical["relations"], list) or not physical["relations"]:
            raise _error(
                f"{pointer}/physical/relations",
                "type",
                "Dataset physical relations must be nonempty",
            )
        relation_names: list[str] = []
        for relation_index, relation in enumerate(physical["relations"]):
            relation_pointer = f"{pointer}/physical/relations/{relation_index}"
            if not isinstance(relation, dict):
                raise _error(relation_pointer, "type", "Physical relation must be an object")
            _require_keys(relation, {"name", "kind"}, relation_pointer)
            if (
                not isinstance(relation["name"], str)
                or not _IDENTIFIER.fullmatch(relation["name"])
                or relation["kind"] not in {"table", "view", "materialization"}
            ):
                raise _error(relation_pointer, "relation", "Physical relation is unsafe")
            relation_names.append(relation["name"])
        if len(relation_names) != len(set(relation_names)):
            raise _error(
                f"{pointer}/physical/relations",
                "unique",
                "Dataset relations must be unique",
            )
        relations = tuple(relation_names)
        for relation in relations:
            key = (value["store"], relation)
            if key in owned_relations:
                raise RegistryError("A physical relation cannot have two dataset owners")
            owned_relations[key] = dataset_id

        identity = _nonempty_mapping(value["identity"], f"{pointer}/identity")
        _require_keys(identity, {"stable_fields", "version_fields"}, f"{pointer}/identity")
        stable_fields = _string_array(
            identity["stable_fields"],
            f"{pointer}/identity/stable_fields",
            allow_empty=False,
            identifiers=True,
        )
        version_fields = _string_array(
            identity["version_fields"],
            f"{pointer}/identity/version_fields",
            identifiers=True,
        )
        all_identity_fields = set(stable_fields).union(version_fields)
        if set(stable_fields).intersection(version_fields) or all_identity_fields.intersection(
            _MUTABLE_IDENTITY_FIELDS
        ):
            raise _error(
                f"{pointer}/identity",
                "immutable_identity",
                "Dataset identity contains overlapping or mutable fields",
            )

        temporal = _nonempty_mapping(value["temporal"], f"{pointer}/temporal")
        _require_keys(
            temporal,
            {
                "observation_fields",
                "observation_precision",
                "availability_fields",
                "availability_precision",
                "timezone_rule",
                "range_semantics",
                "vintage_modes",
                "history_basis",
                "missingness",
            },
            f"{pointer}/temporal",
        )
        _string_array(
            temporal["observation_fields"],
            f"{pointer}/temporal/observation_fields",
            allow_empty=False,
            identifiers=True,
        )
        _string_array(
            temporal["availability_fields"],
            f"{pointer}/temporal/availability_fields",
            allow_empty=False,
            identifiers=True,
        )
        vintage_modes = _string_array(
            temporal["vintage_modes"],
            f"{pointer}/temporal/vintage_modes",
            allow_empty=False,
        )
        if (
            temporal["observation_precision"] not in {"date", "datetime", "mixed"}
            or temporal["availability_precision"] not in {"date", "datetime", "mixed"}
            or temporal["timezone_rule"] != "source_native_no_conversion"
            or temporal["range_semantics"] != "inclusive"
            or not set(vintage_modes).issubset({"latest", "as_of", "first_release"})
            or temporal["history_basis"] not in {"source_vintage", "local_capture"}
            or temporal["missingness"] not in {"not_applicable", "explicit_null", "explicit_reason"}
        ):
            raise _error(f"{pointer}/temporal", "temporal", "Dataset temporal contract is invalid")
        if value["revision_policy"] not in {
            "immutable_capture",
            "append_version",
            "current_state_capture",
            "derived_rebuild",
        }:
            raise _error(
                f"{pointer}/revision_policy", "enum", "Dataset revision policy is invalid"
            )

        freshness = _nonempty_mapping(value["freshness"], f"{pointer}/freshness")
        _require_keys(
            freshness,
            {
                "cadence",
                "expected_lag",
                "stale_after",
                "measured_from",
                "if_new",
                "health_severity",
            },
            f"{pointer}/freshness",
        )
        if (
            freshness["cadence"]
            not in {"intraday", "daily", "weekly", "monthly", "event_driven", "manual"}
            or not isinstance(freshness["expected_lag"], str)
            or not _DURATION.fullmatch(freshness["expected_lag"])
            or not isinstance(freshness["stale_after"], str)
            or not _DURATION.fullmatch(freshness["stale_after"])
            or freshness["measured_from"]
            not in {"source_period", "source_published_at", "available_at", "successful_capture"}
            or not isinstance(freshness["if_new"], bool)
            or freshness["health_severity"] not in {"informational", "warning", "critical"}
        ):
            raise _error(f"{pointer}/freshness", "freshness", "Dataset freshness is invalid")

        quality = _nonempty_mapping(
            value["quality_contract"], f"{pointer}/quality_contract"
        )
        _require_keys(
            quality,
            {"missingness", "units", "rules", "required_warnings"},
            f"{pointer}/quality_contract",
        )
        if (
            not isinstance(quality["missingness"], str)
            or not quality["missingness"]
            or not isinstance(quality["units"], str)
            or not quality["units"]
        ):
            raise _error(
                f"{pointer}/quality_contract", "quality", "Dataset quality contract is invalid"
            )
        _string_array(
            quality["rules"], f"{pointer}/quality_contract/rules", allow_empty=False
        )
        _string_array(
            quality["required_warnings"], f"{pointer}/quality_contract/required_warnings"
        )
        collector_ids = _stable_identifier_array(
            value["collector_ids"], f"{pointer}/collector_ids"
        )
        tool_ids = _stable_identifier_array(value["tool_ids"], f"{pointer}/tool_ids")
        dashboard_ids = _stable_identifier_array(
            value["dashboard_ids"], f"{pointer}/dashboard_ids"
        )
        export_ids = _stable_identifier_array(
            value["export_ids"], f"{pointer}/export_ids"
        )
        if not value["active"] and any(
            (collector_ids, tool_ids, dashboard_ids, export_ids)
        ):
            raise _error(pointer, "inactive", "Inactive datasets cannot have active consumers")
        datasets.append(
            DatasetDeclaration(
                dataset_id,
                value["version"],
                value["store"],
                value["layer"],
                relations,
                physical,
                identity,
                temporal,
                value["revision_policy"],
                freshness,
                quality,
                collector_ids,
                tool_ids,
                dashboard_ids,
                export_ids,
                value["active"],
            )
        )

    compatibility = raw["compatibility_target"]
    if not isinstance(compatibility, dict):
        raise RegistryError("Compatibility target must be an object")
    _require_keys(
        compatibility,
        {
            "api_version",
            "status",
            "reserved_tool_names",
            "stage1_tool_names",
            "stage1_milestone_status",
        },
        "/compatibility_target",
    )
    if (
        compatibility["api_version"] != "1.0"
        or compatibility["status"] != "accepted_incremental"
        or compatibility["stage1_milestone_status"] != "validated_non_active"
    ):
        raise RegistryError("Stage 1 compatibility metadata is invalid")
    reserved = _stable_identifier_array(
        compatibility.get("reserved_tool_names"),
        "/compatibility_target/reserved_tool_names",
        allow_empty=False,
    )
    if tuple(reserved) != PUBLIC_TOOL_NAMES or len(set(reserved)) != 57:
        raise RegistryError("Compatibility target must preserve the exact 57-name inventory")
    stage1_tool_names = _stable_identifier_array(
        compatibility.get("stage1_tool_names"),
        "/compatibility_target/stage1_tool_names",
        allow_empty=False,
    )
    if stage1_tool_names != STAGE1_TOOL_NAMES:
        raise RegistryError("Stage 1 compatibility subset must contain exactly two tools")

    profiles = {profile.name: profile for profile in current_tool_profiles()}
    tools: list[Mapping[str, Any]] = []
    tool_names: list[str] = []
    family_counts = {family: 0 for family in CURRENT_FAMILY_COUNTS}
    dataset_store_by_id = {item.id: item.store for item in datasets}
    required_tool_keys = {
        "id",
        "family",
        "api_version",
        "version",
        "operation_version",
        "lifecycle",
        "compatibility",
        "description",
        "assumptions",
        "handler",
        "operation_graph_id",
        "read_only",
        "stores",
        "datasets",
        "input_type",
        "input_schema_id",
        "input_schema",
        "output_type",
        "output_schema_id",
        "output_schema",
        "examples",
        "workload_bounds",
        "cost_model",
        "timeout_class",
        "availability_policy",
        "live_capability",
        "contracts",
        "composable",
        "observability",
        "owner",
        "review_requirements",
    }
    for index, value in enumerate(raw["tools"]):
        pointer = f"/tools/{index}"
        if not isinstance(value, dict):
            raise _error(pointer, "type", "Tool declaration must be an object")
        _require_keys(value, required_tool_keys, pointer)
        tool_id = _stable_identifier(value["id"], f"{pointer}/id")
        profile = profiles.get(tool_id)
        if profile is None or tool_id in tool_names:
            raise _error(f"{pointer}/id", "inventory", "Tool is not in the reviewed active inventory")
        if (
            value["handler"] != profile.operation_graph_id
            or value["operation_graph_id"] != profile.operation_graph_id
            or value["operation_graph_id"] not in OPERATION_GRAPH_IDS
            or not isinstance(value["handler"], str)
            or not _HANDLER.fullmatch(value["handler"])
        ):
            raise _error(f"{pointer}/operation_graph_id", "handler", "Tool operation graph is not registered")
        if (
            value["family"] != profile.family
            or value["api_version"] != "1.0"
            or not isinstance(value["version"], str)
            or not _SEMVER.fullmatch(value["version"])
            or not isinstance(value["operation_version"], str)
            or not _SEMVER.fullmatch(value["operation_version"])
            or value["lifecycle"] not in {
                "proposed",
                "experimental",
                "stable",
                "deprecated",
                "retired",
            }
            or value["read_only"] is not True
        ):
            raise _error(pointer, "metadata", "Tool version, family, lifecycle, or access drifted")
        compatibility_value = value["compatibility"]
        if not isinstance(compatibility_value, dict):
            raise _error(f"{pointer}/compatibility", "type", "Compatibility must be an object")
        _require_keys(
            compatibility_value,
            {"status", "predecessor"},
            f"{pointer}/compatibility",
        )
        expected_compatibility = (
            "additive_native_v1"
            if tool_id in ADDITIVE_PUBLIC_TOOL_NAMES
            else (
                "recovered_fixture_validated"
                if tool_id in STAGE1_TOOL_NAMES
                else "forward_reconstructed_v1"
            )
        )
        if (
            compatibility_value["status"] != expected_compatibility
            or compatibility_value["predecessor"] is not None
        ):
            raise _error(f"{pointer}/compatibility", "compatibility", "Tool compatibility status drifted")
        if not isinstance(value["description"], str) or not value["description"]:
            raise _error(f"{pointer}/description", "type", "Tool description is required")
        _string_array(value["assumptions"], f"{pointer}/assumptions", allow_empty=False)
        _string_array(
            value["review_requirements"],
            f"{pointer}/review_requirements",
            allow_empty=False,
        )
        tool_stores = _stable_identifier_array(value["stores"], f"{pointer}/stores")
        tool_datasets = _stable_identifier_array(value["datasets"], f"{pointer}/datasets")
        if (
            tool_stores != profile.stores
            or tool_datasets != profile.datasets
            or not set(tool_stores).issubset(expected_roles)
            or not set(tool_datasets).issubset(dataset_ids)
            or any(dataset_store_by_id[item] not in tool_stores for item in tool_datasets)
        ):
            raise _error(f"{pointer}/datasets", "routing", "Tool store or dataset routing drifted")
        for name in ("input_type", "output_type"):
            if not isinstance(value[name], str) or not value[name]:
                raise _error(f"{pointer}/{name}", "type", "Tool contract type is required")
        expected_input_id = f"urn:quant-data:tool:{tool_id}:input:1.0.0"
        expected_output_id = f"urn:quant-data:tool:{tool_id}:output:1.0.0"
        contract_catalog = (
            versioned_schema_contracts
            if tool_id in ADDITIVE_PUBLIC_TOOL_NAMES
            else schema_contracts
        )
        if (
            value["input_schema_id"] != expected_input_id
            or value["output_schema_id"] != expected_output_id
            or contract_catalog.get(expected_input_id) != value["input_schema"]
            or contract_catalog.get(expected_output_id) != value["output_schema"]
        ):
            raise _error(f"{pointer}/input_schema_id", "schema_reference", "Tool schema reference or bytes drifted")
        _validate_strict_schema(value["input_schema"], f"{pointer}/input_schema")
        _validate_strict_schema(value["output_schema"], f"{pointer}/output_schema")
        examples = value["examples"]
        if not isinstance(examples, list) or not examples:
            raise _error(f"{pointer}/examples", "examples", "Every public tool needs an example")
        for example_index, example in enumerate(examples):
            try:
                validate_schema(example, value["input_schema"])
            except ValidationError as exc:
                raise _error(
                    f"{pointer}/examples/{example_index}",
                    "example",
                    "Tool example does not satisfy its generated input schema",
                ) from exc
        bounds = value["workload_bounds"]
        if not isinstance(bounds, dict):
            raise _error(f"{pointer}/workload_bounds", "type", "Workload bounds must be an object")
        _require_keys(
            bounds,
            {
                "max_rows",
                "max_series",
                "max_operations",
                "max_request_bytes",
                "max_response_bytes",
            },
            f"{pointer}/workload_bounds",
        )
        bound_caps = {
            "max_rows": 10000,
            "max_series": 20,
            "max_operations": 5000000,
            "max_request_bytes": MAX_JSON_BYTES,
            "max_response_bytes": MAX_JSON_BYTES,
        }
        if any(
            isinstance(bounds[name], bool)
            or not isinstance(bounds[name], int)
            or not 1 <= bounds[name] <= bound_caps[name]
            for name in bound_caps
        ):
            raise _error(f"{pointer}/workload_bounds", "bounds", "Tool workload bounds are invalid")
        cost = value["cost_model"]
        if not isinstance(cost, dict):
            raise _error(f"{pointer}/cost_model", "type", "Cost model must be an object")
        _require_keys(cost, {"expression", "deterministic"}, f"{pointer}/cost_model")
        if cost != {
            "expression": "rows + series + operations",
            "deterministic": True,
        }:
            raise _error(f"{pointer}/cost_model", "cost_model", "Tool cost model drifted")
        if value["timeout_class"] != "interactive_5s":
            raise _error(f"{pointer}/timeout_class", "timeout", "Tool timeout class drifted")
        availability = value["availability_policy"]
        if not isinstance(availability, dict) or not availability:
            raise _error(f"{pointer}/availability_policy", "type", "Availability policy must be an object")
        live = value["live_capability"]
        if not isinstance(live, dict):
            raise _error(f"{pointer}/live_capability", "type", "Live capability must be an object")
        _require_keys(
            live,
            {"possible", "capability_id", "offline_status"},
            f"{pointer}/live_capability",
        )
        expected_capability = profile.live_capability
        if live != {
            "possible": expected_capability is not None,
            "capability_id": expected_capability,
            "offline_status": "disabled" if expected_capability else "not_applicable",
        }:
            raise _error(f"{pointer}/live_capability", "capability", "Live capability drifted")
        contracts = value["contracts"]
        if not isinstance(contracts, dict):
            raise _error(f"{pointer}/contracts", "type", "Semantic contracts must be an object")
        _require_keys(
            contracts,
            {"availability", "point_in_time", "returns"},
            f"{pointer}/contracts",
        )
        composable = value["composable"]
        if not isinstance(composable, dict):
            raise _error(f"{pointer}/composable", "type", "Composable metadata must be an object")
        _require_keys(
            composable,
            {"input_types", "output_types"},
            f"{pointer}/composable",
        )
        _string_array(composable["input_types"], f"{pointer}/composable/input_types")
        _string_array(
            composable["output_types"],
            f"{pointer}/composable/output_types",
            allow_empty=False,
        )
        if (
            value["observability"] != "metadata_only"
            or not isinstance(value["owner"], str)
            or not value["owner"]
        ):
            raise _error(pointer, "ownership", "Tool observability or owner is invalid")
        family_counts[profile.family] += 1
        tool_names.append(tool_id)
        tools.append(value)
    if (
        tuple(tool_names) != CURRENT_PUBLIC_TOOL_NAMES
        or len(set(tool_names)) != len(CURRENT_PUBLIC_TOOL_NAMES)
    ):
        raise RegistryError("Canonical registry must expose the reviewed active tools")
    if family_counts != CURRENT_FAMILY_COUNTS:
        raise RegistryError("Canonical active tool family counts drifted")
    macro_output = tools[CURRENT_PUBLIC_TOOL_NAMES.index("macro.get_series")]["output_schema"]
    describe_series = tools[CURRENT_PUBLIC_TOOL_NAMES.index("timeseries.describe")]["input_schema"]["properties"]["series"]
    if describe_series != macro_output:
        raise RegistryError("Composable describe input must exactly match macro TimeSeries output")

    expected_version_policies = build_tool_version_policies()
    if raw["tool_versions"] != list(expected_version_policies):
        raise _error(
            "/tool_versions",
            "version_contract",
            "Versioned public tool contracts drifted from their typed declarations",
        )
    tool_version_policies = tuple(raw["tool_versions"])
    if tuple(policy["tool"] for policy in tool_version_policies) != (
        VERSIONED_TOOL_NAMES
    ):
        raise _error(
            "/tool_versions",
            "inventory",
            "The reviewed v2 public-tool inventory drifted",
        )
    seen_tool_versions: set[tuple[str, str]] = set()
    for policy_index, policy in enumerate(tool_version_policies):
        policy_pointer = f"/tool_versions/{policy_index}"
        _require_keys(
            policy,
            {
                "tool",
                "default_version",
                "selector_field",
                "variants",
                "deprecations",
            },
            policy_pointer,
        )
        if (
            policy["default_version"] != "1.0.0"
            or policy["selector_field"] != "tool_version"
            or not isinstance(policy["variants"], list)
            or len(policy["variants"])
            != (
                8
                if policy["tool"] == "market.technical_indicators"
                else (
                    3
                    if policy["tool"] == "econometrics.regression"
                    else (
                        4
                        if policy["tool"] == "news.search"
                        else 2
                        if policy["tool"]
                        in {
                            "econometrics.rolling_regression",
                            "econometrics.stationarity",
                            "market.cross_sectional_performance",
                            "energy.get_electricity_retail_sales",
                            "energy.get_weekly_fundamentals",
                            "company.get_fundamentals",
                        }
                        else 1
                    )
                )
            ) + int(
                policy["tool"] in PRICE_BASIS_VERSIONS
                and policy["tool"] not in NEW_PRICE_BASIS_POLICIES
            )
            + int(policy["tool"] in {"company.get_fundamentals", "company.get_share_count_history"})
            or not isinstance(policy["deprecations"], list)
            or len(policy["deprecations"]) != 1
        ):
            raise _error(
                policy_pointer,
                "selection_policy",
                "Tool version selection or deprecation policy is invalid",
            )
        base = tools[CURRENT_PUBLIC_TOOL_NAMES.index(policy["tool"])]
        variant = policy["variants"][0]
        if not isinstance(variant, dict):
            raise _error(
                f"{policy_pointer}/variants/0",
                "type",
                "Tool version variant must be an object",
            )
        version_key = (str(variant.get("id")), str(variant.get("version")))
        if version_key in seen_tool_versions:
            raise _error(
                f"{policy_pointer}/variants/0/version",
                "unique",
                "Tool version variants must be unique",
            )
        seen_tool_versions.add(version_key)
        if (
            variant.get("id") != policy["tool"]
            or variant.get("version") != "2.0.0"
            or variant.get("operation_version") != "2.0.0"
            or variant.get("api_version") != "1.0"
            or variant.get("read_only") is not True
            or variant.get("operation_graph_id") not in VERSIONED_OPERATION_GRAPH_IDS
            or variant.get("handler") != variant.get("operation_graph_id")
            or variant.get("input_schema_id")
            != f"urn:quant-data:tool:{policy['tool']}:input:2.0.0"
            or variant.get("output_schema_id")
            != f"urn:quant-data:tool:{policy['tool']}:output:2.0.0"
            or versioned_schema_contracts.get(variant["input_schema_id"])
            != variant.get("input_schema")
            or versioned_schema_contracts.get(variant["output_schema_id"])
            != variant.get("output_schema")
        ):
            raise _error(
                f"{policy_pointer}/variants/0",
                "variant_contract",
                "Versioned tool schema, operation, or access contract is invalid",
            )
        _validate_strict_schema(
            variant["input_schema"], f"{policy_pointer}/variants/0/input_schema"
        )
        _validate_strict_schema(
            variant["output_schema"], f"{policy_pointer}/variants/0/output_schema"
        )
        variant_datasets = _stable_identifier_array(
            variant["datasets"], f"{policy_pointer}/variants/0/datasets"
        )
        variant_stores = _stable_identifier_array(
            variant["stores"], f"{policy_pointer}/variants/0/stores"
        )
        if policy["tool"] in VERSIONED_CANONICAL_MACRO_TOOLS:
            expected_variant_stores = ("macro",)
            expected_variant_datasets = (
                "fixture.macro.rtdsm_employ",
                "fixture.macro.rtdsm_employ_evidence",
                "fixture.macro.stage3_catalog",
                "macro.official_vintages",
                "macro.official_vintages_evidence",
            )
        elif policy["tool"] in VERSIONED_MARKET_RETURN_TOOLS:
            expected_variant_stores = ("market",)
            expected_variant_datasets = (
                "market.stage10.daily_prices",
                "market.stage10.source_evidence",
                "market.stage10.instruments",
            )
        elif policy["tool"] == "company.get_guidance_history":
            from .tool_platform.transcript_contracts import RAW_DATASET, STRUCTURED_DATASET
            expected_variant_stores = ("company",)
            expected_variant_datasets = (RAW_DATASET, STRUCTURED_DATASET)
        elif policy["tool"] in VERSIONED_COMPANY_FILING_TOOLS:
            expected_variant_stores = ("company",)
            expected_variant_datasets = ("fixture.company.filings",)
        elif policy["tool"] in VERSIONED_COMPANY_SHARE_COUNT_TOOLS:
            expected_variant_stores = ("company",)
            expected_variant_datasets = ("fixture.company.fundamentals",)
        elif policy["tool"] in VERSIONED_MARKET_INSTRUMENT_SEARCH_TOOLS:
            expected_variant_stores = ("market",)
            expected_variant_datasets = ("market.stage10.instruments",)
        elif policy["tool"] in VERSIONED_NEWS_TOOLS:
            expected_variant_stores = ("news",)
            expected_variant_datasets = (
                "news.fmp.stock_latest_current_evidence",
                "news.fmp.stock_latest_current_articles",
            )
        elif policy["tool"] in VERSIONED_OPTIONS_ACCESS_TOOLS:
            expected_variant_stores = ("market",)
            expected_variant_datasets = (
                "fixture.market.instruments",
                "fixture.market.option_capture_evidence",
                "fixture.market.options",
            )
        elif policy["tool"] in {"company.get_earnings_calendar", "company.get_earnings_setup", "company.get_consensus_history", "company.get_estimate_revisions"}:
            from .tool_platform.retained_research_contracts import stores_for, datasets_for
            expected_variant_stores = stores_for(policy["tool"])
            expected_variant_datasets = datasets_for(policy["tool"])
        elif policy["tool"] in VERSIONED_INVESTMENT_ANALYSIS_TOOLS:
            expected_variant_stores = tuple(variant_stores)
            expected_variant_datasets = tuple(variant_datasets)
        elif policy["tool"] in {"market.get_price_series", "research.news_event_impact", "company.get_transcript"}:
            expected_variant_stores = tuple(base["stores"])
            expected_variant_datasets = tuple(base["datasets"])
        elif policy["tool"] in VERSIONED_ETF_SNAPSHOT_TOOLS:
            expected_variant_stores = ("market",)
            expected_variant_datasets = (
                "market.stage10.instruments",
                "market.stage10.daily_prices",
                "market.stage10.source_evidence",
            )
        else:
            expected_variant_stores = ()
            expected_variant_datasets = ()
        if (
            variant_stores != expected_variant_stores
            or tuple(variant_datasets) != expected_variant_datasets
            or any(
                dataset_store_by_id[item] not in variant_stores
                for item in variant_datasets
            )
        ):
            raise _error(
                f"{policy_pointer}/variants/0/datasets",
                "routing",
                "Versioned tool dataset routing is invalid",
            )
        for example_index, example in enumerate(variant["examples"]):
            try:
                validate_schema(example, variant["input_schema"])
            except ValidationError as exc:
                raise _error(
                    f"{policy_pointer}/variants/0/examples/{example_index}",
                    "example",
                    "Versioned tool example does not satisfy its input schema",
                ) from exc
        deprecation = policy["deprecations"][0]
        if (
            deprecation.get("version") != base["version"]
            or deprecation.get("code") != "tool_version_deprecated"
            or deprecation.get("replacement")
            != {
                "tool": policy["tool"],
                "version": (
                    policy["variants"][-1]["version"]
                    if policy["tool"] == "news.search"
                    else variant["version"]
                ),
            }
            or deprecation.get("removal")
            != {"status": "not_scheduled", "milestone": None}
        ):
            raise _error(
                f"{policy_pointer}/deprecations/0",
                "deprecation",
                "Tool deprecation metadata is invalid",
            )
        for variant_index, additional_variant in enumerate(
            policy["variants"][1:], start=1
        ):
            is_v3_variant = (
                policy["tool"] == "econometrics.regression"
                and variant_index == 2
            )
            is_indicator_v22_variant = (
                policy["tool"] == "market.technical_indicators"
                and variant_index == 2
            )
            is_indicator_v23_variant = (
                policy["tool"] == "market.technical_indicators"
                and variant_index == 3
            )
            is_indicator_v24_variant = (
                policy["tool"] == "market.technical_indicators"
                and variant_index == 4
            )
            is_indicator_v25_variant = (
                policy["tool"] == "market.technical_indicators"
                and variant_index == 5
            )
            is_indicator_v27_variant = (
                policy["tool"] == "market.technical_indicators"
                and variant_index == 7
            )
            is_indicator_v26_variant = (
                policy["tool"] == "market.technical_indicators"
                and variant_index == 6
            )
            is_news_v23_variant = (
                policy["tool"] == "news.search" and variant_index == 3
            )
            is_news_v22_variant = (
                policy["tool"] == "news.search" and variant_index == 2
            )
            basis_pair = PRICE_BASIS_VERSIONS.get(policy["tool"])
            is_price_basis_variant = (
                basis_pair is not None and additional_variant.get("version") == basis_pair[1]
            )
            is_sharadar_variant = (
                policy["tool"] in {"company.get_fundamentals", "company.get_share_count_history"}
                and additional_variant.get("version") == "3.0.0"
            )
            expected_version = (
                "3.0.0" if is_sharadar_variant else
                basis_pair[1] if is_price_basis_variant else
                "2.3.0" if is_news_v23_variant else
                "3.0.0"
                if is_v3_variant
                else "2.2.0"
                if is_news_v22_variant
                else "2.7.0"
                if is_indicator_v27_variant
                else "2.6.0"
                if is_indicator_v26_variant
                else "2.5.0"
                if is_indicator_v25_variant
                else "2.4.0"
                if is_indicator_v24_variant
                else "2.3.0"
                if is_indicator_v23_variant
                else "2.2.0"
                if is_indicator_v22_variant
                else "2.1.0"
            )
            expected_graph_suffix = (
                "v3" if is_sharadar_variant else
                "v" + basis_pair[1][:-2].replace(".", "_") if is_price_basis_variant else
                "v2_3" if is_news_v23_variant else
                "v3"
                if is_v3_variant
                else "v2_2"
                if is_news_v22_variant
                else "v2_7"
                if is_indicator_v27_variant
                else "v2_6"
                if is_indicator_v26_variant
                else "v2_5"
                if is_indicator_v25_variant
                else "v2_4"
                if is_indicator_v24_variant
                else "v2_3"
                if is_indicator_v23_variant
                else "v2_2"
                if is_indicator_v22_variant
                else "v2_1"
            )

            variant_pointer = f"{policy_pointer}/variants/{variant_index}"
            if not isinstance(additional_variant, dict):
                raise _error(
                    variant_pointer,
                    "type",
                    "Tool version variant must be an object",
                )
            version_key = (
                str(additional_variant.get("id")),
                str(additional_variant.get("version")),
            )
            if version_key in seen_tool_versions:
                raise _error(
                    f"{variant_pointer}/version",
                    "unique",
                    "Tool version variants must be unique",
                )
            seen_tool_versions.add(version_key)
            if (
                not is_price_basis_variant and not is_sharadar_variant and policy["tool"]
                not in {
                    "econometrics.regression",
                    "econometrics.rolling_regression",
                    "econometrics.stationarity",
                    "market.technical_indicators",
                    "market.cross_sectional_performance",
                    "energy.get_electricity_retail_sales",
                    "energy.get_weekly_fundamentals",
                    "company.get_fundamentals",
                    "news.search",
                }
                or additional_variant.get("id") != policy["tool"]
                or additional_variant.get("version") != expected_version
                or additional_variant.get("operation_version") != expected_version
                or additional_variant.get("api_version") != "1.0"
                or additional_variant.get("read_only") is not True
                or additional_variant.get("operation_graph_id")
                != f"tool_platform.{policy['tool']}.{expected_graph_suffix}"
                or additional_variant.get("operation_graph_id")
                not in VERSIONED_OPERATION_GRAPH_IDS
                or additional_variant.get("handler")
                != additional_variant.get("operation_graph_id")
                or additional_variant.get("input_schema_id")
                != f"urn:quant-data:tool:{policy['tool']}:input:{expected_version}"
                or additional_variant.get("output_schema_id")
                != f"urn:quant-data:tool:{policy['tool']}:output:{expected_version}"
                or versioned_schema_contracts.get(
                    additional_variant["input_schema_id"]
                )
                != additional_variant.get("input_schema")
                or versioned_schema_contracts.get(
                    additional_variant["output_schema_id"]
                )
                != additional_variant.get("output_schema")
            ):
                raise _error(
                    variant_pointer,
                    "variant_contract",
                    "Versioned tool schema, operation, or access contract is invalid",
                )
            _validate_strict_schema(
                additional_variant["input_schema"],
                f"{variant_pointer}/input_schema",
            )
            _validate_strict_schema(
                additional_variant["output_schema"],
                f"{variant_pointer}/output_schema",
            )
            additional_datasets = _stable_identifier_array(
                additional_variant["datasets"],
                f"{variant_pointer}/datasets",
            )
            additional_stores = _stable_identifier_array(
                additional_variant["stores"],
                f"{variant_pointer}/stores",
            )
            store_backed_successor = policy["tool"] in {
                "market.get_returns",
                "market.get_forward_returns",
                "market.cross_sectional_performance",
                "energy.get_electricity_retail_sales",
                "energy.get_weekly_fundamentals",
                "company.get_fundamentals",
            }
            if is_sharadar_variant:
                from .tool_platform.sharadar_company_contracts import DATASETS
                if additional_datasets != DATASETS or additional_stores != ("market", "company"):
                    raise _error(f"{variant_pointer}/datasets", "routing", "Sharadar successor routing is invalid")
            elif policy["tool"] == "news.search":
                expected_news_datasets = (
                    *tuple(variant["datasets"]),
                    "news.current_multi_source_evidence",
                    "news.current_multi_source_articles",
                )
                if (
                    additional_datasets != expected_news_datasets
                    or additional_stores != tuple(variant["stores"])
                ):
                    raise _error(
                        f"{variant_pointer}/datasets",
                        "routing",
                        "Current-news successor routing is invalid",
                    )
            elif store_backed_successor:
                if (
                    additional_datasets != tuple(variant["datasets"])
                    or additional_stores != tuple(variant["stores"])
                ):
                    raise _error(
                        f"{variant_pointer}/datasets",
                        "routing",
                        "Store-backed successors must preserve v2 routing",
                    )
            elif additional_datasets or additional_stores:
                raise _error(
                    f"{variant_pointer}/datasets",
                    "routing",
                    "Additional analytical variants must remain store-free",
                )
            for example_index, example in enumerate(
                additional_variant["examples"]
            ):
                try:
                    validate_schema(
                        example, additional_variant["input_schema"]
                    )
                except ValidationError as exc:
                    raise _error(
                        f"{variant_pointer}/examples/{example_index}",
                        "example",
                        "Versioned tool example does not satisfy its input schema",
                    ) from exc


    collectors = tuple(raw["collectors"])

    collector_ids: set[str] = set()
    for index, collector in enumerate(collectors):
        if not isinstance(collector, dict):
            raise _error(f"/collectors/{index}", "type", "Collector must be an object")
        pointer = f"/collectors/{index}"
        _require_keys(
            collector,
            {
                "id",
                "version",
                "handler",
                "network",
                "input_datasets",
                "output_datasets",
                "semantic_identity",
                "mutation_policy",
                "workload_bounds",
                "retry_policy",
                "configuration_env",
                "physical_locks",
                "schedule_eligibility",
            },
            pointer,
        )
        collector_id = _stable_identifier(collector["id"], f"{pointer}/id")
        if (
            collector_id in collector_ids
            or not isinstance(collector["version"], str)
            or not _SEMVER.fullmatch(collector["version"])
            or not isinstance(collector["handler"], str)
            or not _HANDLER.fullmatch(collector["handler"])
            or not isinstance(collector["network"], bool)
        ):
            raise _error(pointer, "collector", "Collector metadata is invalid")
        collector_ids.add(collector_id)
        inputs = _stable_identifier_array(
            collector["input_datasets"], f"{pointer}/input_datasets"
        )
        outputs = _stable_identifier_array(
            collector["output_datasets"],
            f"{pointer}/output_datasets",
            allow_empty=False,
        )
        if not set(inputs).issubset(dataset_ids) or not set(outputs).issubset(dataset_ids):
            raise _error(f"/collectors/{index}/output_datasets", "reference", "Collector output is invalid")
        semantic_identity = _nonempty_mapping(
            collector["semantic_identity"], f"{pointer}/semantic_identity"
        )
        _require_keys(
            semantic_identity, {"includes", "excludes"}, f"{pointer}/semantic_identity"
        )
        includes = _string_array(
            semantic_identity["includes"],
            f"{pointer}/semantic_identity/includes",
            allow_empty=False,
        )
        excludes = _string_array(
            semantic_identity["excludes"], f"{pointer}/semantic_identity/excludes"
        )
        if set(includes).intersection(excludes) or "request_scope" not in includes:
            raise _error(
                f"{pointer}/semantic_identity",
                "semantic_identity",
                "Collector identity must be scope-bearing and non-overlapping",
            )
        mutation_policy = _nonempty_mapping(
            collector["mutation_policy"], f"{pointer}/mutation_policy"
        )
        _require_keys(mutation_policy, {"mode", "unchanged"}, f"{pointer}/mutation_policy")
        if (
            not isinstance(mutation_policy["mode"], str)
            or not mutation_policy["mode"]
            or mutation_policy["unchanged"] != "zero_persistent_writes"
        ):
            raise _error(
                f"{pointer}/mutation_policy", "mutation", "Collector mutation policy is invalid"
            )
        workload = _nonempty_mapping(
            collector["workload_bounds"], f"{pointer}/workload_bounds"
        )
        _require_keys(
            workload,
            {"max_requests", "max_rows", "max_bytes", "max_seconds"},
            f"{pointer}/workload_bounds",
        )
        stage11_collector = collector_id in _STAGE11_COLLECTOR_IDS
        fmp_stock_latest_collector = collector_id in {
            _FMP_STOCK_LATEST_COLLECTOR_ID,
            _FMP_STOCK_LATEST_CURRENT_COLLECTOR_ID,
            _CURRENT_MULTI_SOURCE_COLLECTOR_ID,
        }
        max_workload_bytes = (
            268_435_456
            if collector_id == _ALPACA_ETF_OPTION_COLLECTOR_ID
            else 67_108_864
            if (
                fmp_stock_latest_collector
                or collector_id == _SEC_MARKET_COMPANYFACTS_COLLECTOR_ID
            )
            else 16_777_216
            if (
                collector_id
                in {
                    "fmp.market.stage10_daily_history",
                    _IWM_ETF_DAILY_HISTORY_COLLECTOR_ID,
                    _IWM_ETF_DAILY_HISTORY_BACKFILL_COLLECTOR_ID,
                }
                or collector_id
                == "philadelphia_fed.macro.live_employment_vintages"
                or stage11_collector
                or collector_id == _FMP_TREASURY_YIELD_CURVE_COLLECTOR_ID
                or collector_id == _NYFED_OVERNIGHT_RATES_COLLECTOR_ID
                or collector_id == _NYFED_REPO_FACILITIES_COLLECTOR_ID
                or collector_id == _NYFED_SOMA_COLLECTOR_ID
                or collector_id in _OFFICIAL_CONDITIONS_COLLECTOR_IDS
                or collector_id == _NYFED_CMDI_COLLECTOR_ID
                or collector_id in _OFFICIAL_MACRO_EXTENSION_COLLECTOR_IDS
                or collector_id == _BLS_PRICE_WAGE_PRODUCTIVITY_COLLECTOR_ID
                or collector_id == _BEA_PERSONAL_INCOME_COLLECTOR_ID
                or collector_id == _SEC_AAPL_COMPANYFACTS_COLLECTOR_ID
            )
            else MAX_JSON_BYTES
        )
        if collector_id == "fmp.company.statement_history":
            max_workload_bytes = 8_388_608
        if collector_id in ("nasdaq.company.sharadar_sf1","sharadar.company.direct_sf1"):
            max_workload_bytes = 268_435_456
        if collector_id in ("nasdaq.company.sharadar_definitions","sharadar.company.direct_definitions","fmp.company.short_descriptions"):
            max_workload_bytes = 33_554_432
        if collector_id in ("local.market.collection_manifest","local.market.selected_instruments"):
            max_workload_bytes = 67_108_864
        if collector_id in _MACRO_DATABASE_EXPANSION_COLLECTOR_SPECS:
            max_workload_bytes = _MACRO_DATABASE_EXPANSION_COLLECTOR_SPECS[collector_id][3]
        if any(
            isinstance(workload[name], bool)
            or not isinstance(workload[name], int)
            or workload[name] < (0 if collector_id in ("local.market.collection_manifest","local.market.selected_instruments") and name == "max_requests" else 1)
            or workload[name] > max_workload_bytes
            for name in workload
        ) or workload["max_rows"] > (
            _MACRO_DATABASE_EXPANSION_COLLECTOR_SPECS[collector_id][2]
            if collector_id in _MACRO_DATABASE_EXPANSION_COLLECTOR_SPECS
            else
            900_016
            if collector_id == _ALPACA_ETF_OPTION_COLLECTOR_ID
            else 500_000
            if collector_id == "philadelphia_fed.macro.live_employment_vintages"
            else 1_000
            if fmp_stock_latest_collector
            else 40_000
            if collector_id == "eia.macro.stage11_electricity_retail_history"
            else 30_000
            if collector_id
            in {
                "fmp.market.stage10_daily_history",
                _IWM_ETF_DAILY_HISTORY_COLLECTOR_ID,
                _IWM_ETF_DAILY_HISTORY_BACKFILL_COLLECTOR_ID,
            }
            else 20_000
            if collector_id
            in {
                _FMP_TREASURY_YIELD_CURVE_COLLECTOR_ID,
                _NYFED_OVERNIGHT_RATES_COLLECTOR_ID,
                _NYFED_REPO_FACILITIES_COLLECTOR_ID,
                _NYFED_SOMA_COLLECTOR_ID,
                *_OFFICIAL_CONDITIONS_COLLECTOR_IDS,
                _NYFED_CMDI_COLLECTOR_ID,
                *_OFFICIAL_MACRO_EXTENSION_COLLECTOR_IDS,
                _BLS_PRICE_WAGE_PRODUCTIVITY_COLLECTOR_ID,
                _BEA_PERSONAL_INCOME_COLLECTOR_ID,
            }
            else 50_000
            if collector_id == _SEC_MARKET_COMPANYFACTS_COLLECTOR_ID
            else 100_000
            if collector_id in ("nasdaq.company.sharadar_sf1","sharadar.company.direct_sf1")
            else 10_000
        ):
            raise _error(f"{pointer}/workload_bounds", "bounds", "Collector bounds are invalid")
        retry = _nonempty_mapping(collector["retry_policy"], f"{pointer}/retry_policy")
        _require_keys(
            retry,
            {"transient_classes", "max_attempts", "backoff", "honor_retry_after"},
            f"{pointer}/retry_policy",
        )
        _string_array(retry["transient_classes"], f"{pointer}/retry_policy/transient_classes")
        if (
            isinstance(retry["max_attempts"], bool)
            or not isinstance(retry["max_attempts"], int)
            or not 1 <= retry["max_attempts"] <= 10
            or not isinstance(retry["backoff"], str)
            or not retry["backoff"]
            or not isinstance(retry["honor_retry_after"], bool)
        ):
            raise _error(f"{pointer}/retry_policy", "retry", "Collector retry policy is invalid")
        configuration_env = _string_array(
            collector["configuration_env"], f"{pointer}/configuration_env"
        )
        if collector_id == _FMP_GDP_CPI_CALENDAR_COLLECTOR_ID:
            if (
                collector["version"] != "1.0.0"
                or collector["handler"]
                != "macro.fmp_gdp_cpi_release_calendar"
                or collector["network"] is not True
                or inputs
                or outputs != (_FMP_GDP_CPI_CALENDAR_DATASET_ID,)
                or includes
                != (
                    "request_scope",
                    "normalization_version",
                    "normalized_complete_batch",
                )
                or excludes
                != (
                    "api_key",
                    "captured_at",
                    "http_headers",
                    "source_row_order",
                )
                or mutation_policy
                != {
                    "mode": "append_versions_and_snapshot_membership",
                    "unchanged": "zero_persistent_writes",
                }
                or workload
                != {
                    "max_requests": 1,
                    "max_rows": 2_000,
                    "max_bytes": 1_048_576,
                    "max_seconds": 60,
                }
                or retry
                != {
                    "transient_classes": [],
                    "max_attempts": 1,
                    "backoff": "none_single_attempt",
                    "honor_retry_after": False,
                }
                or configuration_env != ("FMP_API_KEY",)
            ):
                raise _error(
                    pointer,
                    "fmp_macro_calendar",
                    "FMP GDP/CPI calendar collector drifted",
                )
        elif collector_id == _FMP_EMPLOYMENT_CALENDAR_COLLECTOR_ID:
            if (
                collector["version"] != "1.0.0"
                or collector["handler"]
                != "macro.fmp_employment_release_calendar_refresh"
                or collector["network"] is not True
                or inputs
                or outputs != (_FMP_GDP_CPI_CALENDAR_DATASET_ID,)
                or includes
                != (
                    "request_scope",
                    "normalization_version",
                    "normalized_complete_batch",
                )
                or excludes
                != (
                    "api_key",
                    "captured_at",
                    "http_headers",
                    "source_row_order",
                )
                or mutation_policy
                != {
                    "mode": "append_versions_and_snapshot_membership",
                    "unchanged": "zero_persistent_writes",
                }
                or workload
                != {
                    "max_requests": 1,
                    "max_rows": 2_000,
                    "max_bytes": 1_048_576,
                    "max_seconds": 60,
                }
                or retry
                != {
                    "transient_classes": [],
                    "max_attempts": 1,
                    "backoff": "none_single_attempt",
                    "honor_retry_after": False,
                }
                or configuration_env != ("FMP_API_KEY",)
            ):
                raise _error(
                    pointer,
                    "fmp_employment_calendar",
                    "FMP employment calendar collector drifted",
                )
        elif collector_id == _FMP_WHOLESALE_CALENDAR_COLLECTOR_ID:
            if (
                collector["version"] != "1.0.0"
                or collector["handler"] != "macro.fmp_us_economic_calendar_wholesale"
                or collector["network"] is not True
                or inputs
                or outputs != _FMP_CALENDAR_INCREMENTAL_DATASET_IDS
                or includes != (
                    "request_scope",
                    "normalization_version",
                    "normalized_complete_batch",
                    "predecessor_receipt_id",
                )
                or excludes != (
                    "api_key",
                    "captured_at",
                    "http_headers",
                    "source_row_order",
                )
                or mutation_policy != {
                    "mode": "append_event_versions_and_replace_latest_cache",
                    "unchanged": "zero_persistent_writes",
                }
                or workload != {
                    "max_requests": 1,
                    "max_rows": 2_000,
                    "max_bytes": 1_048_576,
                    "max_seconds": 60,
                }
                or retry != {
                    "transient_classes": [],
                    "max_attempts": 1,
                    "backoff": "none_single_attempt",
                    "honor_retry_after": False,
                }
                or configuration_env != ("FMP_API_KEY",)
            ):
                raise _error(
                    pointer,
                    "fmp_wholesale_calendar",
                    "FMP wholesale calendar collector drifted",
                )
        elif collector_id == _FMP_TREASURY_YIELD_CURVE_COLLECTOR_ID:
            if (
                collector["version"] != "1.0.0"
                or collector["handler"] != "macro.fmp_treasury_yield_curve_history"
                or collector["network"] is not True
                or inputs
                or outputs != _FMP_TREASURY_YIELD_CURVE_DATASET_IDS
                or includes != (
                    "request_scope",
                    "normalization_version",
                    "normalized_12_tenor_batch",
                )
                or excludes != (
                    "api_key",
                    "captured_at",
                    "http_headers",
                    "source_row_order",
                    "unknown_provider_fields",
                )
                or mutation_policy != {
                    "mode": "append_versions_and_snapshot_membership",
                    "unchanged": "zero_persistent_writes",
                }
                or workload != {
                    "max_requests": 1,
                    "max_rows": 20_000,
                    "max_bytes": 16_777_216,
                    "max_seconds": 60,
                }
                or retry != {
                    "transient_classes": [],
                    "max_attempts": 1,
                    "backoff": "none_single_attempt",
                    "honor_retry_after": False,
                }
                or configuration_env != ("FMP_API_KEY",)
            ):
                raise _error(
                    pointer,
                    "fmp_treasury_yield_curve",
                    "FMP Treasury yield-curve collector drifted",
                )
        elif collector_id == _NYFED_OVERNIGHT_RATES_COLLECTOR_ID:
            if (
                collector["version"] != "1.0.0"
                or collector["handler"] != "macro.nyfed_overnight_rates_history"
                or collector["network"] is not True
                or inputs
                or outputs != _NYFED_OVERNIGHT_RATES_DATASET_IDS
                or includes != (
                    "request_scope",
                    "normalization_version",
                    "normalized_headline_rate_batch",
                )
                or excludes != (
                    "captured_at",
                    "http_headers",
                    "source_row_order",
                    "unknown_provider_fields",
                    "unsupported_rate_types",
                )
                or mutation_policy != {
                    "mode": "append_versions_and_snapshot_membership",
                    "unchanged": "zero_persistent_writes",
                }
                or workload != {
                    "max_requests": 1,
                    "max_rows": 20_000,
                    "max_bytes": 16_777_216,
                    "max_seconds": 60,
                }
                or retry != {
                    "transient_classes": [],
                    "max_attempts": 1,
                    "backoff": "none_single_attempt",
                    "honor_retry_after": False,
                }
                or configuration_env
            ):
                raise _error(
                    pointer,
                    "nyfed_overnight_rates",
                    "NY Fed overnight-rate collector drifted",
                )
        elif collector_id == _NYFED_REPO_FACILITIES_COLLECTOR_ID:
            if (
                collector["version"] != "1.0.0"
                or collector["handler"]
                != "macro.nyfed_repo_facility_usage_history"
                or collector["network"] is not True
                or inputs
                or outputs != _NYFED_REPO_FACILITIES_DATASET_IDS
                or includes
                != (
                    "request_scope",
                    "normalization_version",
                    "normalized_daily_facility_batch",
                )
                or excludes
                != (
                    "captured_at",
                    "explicit_small_value_exercises",
                    "http_headers",
                    "source_row_order",
                    "unknown_provider_fields",
                )
                or mutation_policy
                != {
                    "mode": "append_versions_and_snapshot_membership",
                    "unchanged": "zero_persistent_writes",
                }
                or workload
                != {
                    "max_requests": 1,
                    "max_rows": 20_000,
                    "max_bytes": 16_777_216,
                    "max_seconds": 60,
                }
                or retry
                != {
                    "transient_classes": [],
                    "max_attempts": 1,
                    "backoff": "none_single_attempt",
                    "honor_retry_after": False,
                }
                or configuration_env
            ):
                raise _error(
                    pointer,
                    "nyfed_repo_facilities",
                    "NY Fed repo-facility collector drifted",
                )
        elif collector_id == _NYFED_SOMA_COLLECTOR_ID:
            if (
                collector["version"] != "1.0.0"
                or collector["handler"] != "macro.nyfed_soma_summary_history"
                or collector["network"] is not True
                or inputs
                or outputs != _NYFED_SOMA_DATASET_IDS
                or includes
                != (
                    "request_scope",
                    "normalization_version",
                    "normalized_summary_components",
                )
                or excludes
                != (
                    "captured_at",
                    "http_headers",
                    "source_row_order",
                )
                or mutation_policy
                != {
                    "mode": "append_summary_versions_and_snapshot_membership",
                    "unchanged": "zero_persistent_writes",
                }
                or workload
                != {
                    "max_requests": 1,
                    "max_rows": 20_000,
                    "max_bytes": 16_777_216,
                    "max_seconds": 60,
                }
                or retry
                != {
                    "transient_classes": [],
                    "max_attempts": 1,
                    "backoff": "none_single_attempt",
                    "honor_retry_after": False,
                }
                or configuration_env
            ):
                raise _error(
                    pointer,
                    "nyfed_soma",
                    "NY Fed SOMA collector drifted",
                )
        elif collector_id in (
            *_OFFICIAL_CONDITIONS_COLLECTOR_IDS,
            _NYFED_CMDI_COLLECTOR_ID,
            *_OFFICIAL_MACRO_EXTENSION_COLLECTOR_IDS,
            _BLS_PRICE_WAGE_PRODUCTIVITY_COLLECTOR_ID,
            _BEA_PERSONAL_INCOME_COLLECTOR_ID,
        ):
            exact = {
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
                "nyfed.macro.cmdi_history": (
                    "macro.nyfed_cmdi_history",
                    1,
                    5_000,
                    60,
                ),
                "treasury_fiscal_data.macro.tga_closing_balance_history": (
                    "macro.treasury_fiscal_tga_history",
                    1,
                    10_000,
                    60,
                ),
                "eia.macro.natural_gas_storage_history": (
                    "macro.eia_natural_gas_storage_history",
                    1,
                    5_000,
                    60,
                ),
                "nber.macro.us_recession_history": (
                    "macro.nber_us_recession_history",
                    1,
                    5_000,
                    60,
                ),
                "bls.macro.price_wage_productivity_history": (
                    "macro.bls_price_wage_productivity_history",
                    1,
                    5_000,
                    60,
                ),
                "bea.macro.personal_income_history": (
                    "macro.bea_personal_income_history",
                    1,
                    5_000,
                    60,
                ),
            }[collector_id]
            expected_configuration_env = (
                ("EIA_API_KEY",)
                if collector_id == "eia.macro.natural_gas_storage_history"
                else ("BEA_API_KEY",)
                if collector_id == _BEA_PERSONAL_INCOME_COLLECTOR_ID
                else ()
            )
            expected_excludes = (
                ("api_key", "captured_at", "http_headers", "source_row_order")
                if expected_configuration_env
                else ("captured_at", "http_headers", "source_row_order")
            )
            if (
                collector["version"] != "1.0.0"
                or collector["handler"] != exact[0]
                or collector["network"] is not True
                or inputs
                or outputs != _OFFICIAL_CONDITIONS_DATASET_IDS
                or includes != (
                    "request_scope",
                    "normalization_version",
                    "normalized_observations",
                )
                or excludes != expected_excludes
                or mutation_policy
                != {
                    "mode": "append_versions_and_snapshot_membership",
                    "unchanged": "zero_persistent_writes",
                }
                or workload
                != {
                    "max_requests": exact[1],
                    "max_rows": exact[2],
                    "max_bytes": 16_777_216,
                    "max_seconds": exact[3],
                }
                or retry
                != {
                    "transient_classes": [],
                    "max_attempts": 1,
                    "backoff": "none_single_attempt",
                    "honor_retry_after": False,
                }
                or configuration_env != expected_configuration_env
            ):
                raise _error(
                    pointer,
                    "official_conditions",
                    "Official conditions collector drifted",
                )
        elif collector_id == "local.market.selected_instruments":
            from quant_data.company.fmp_statement_registry import identity_declaration
            if collector != identity_declaration(raw):
                raise _error(pointer, "selected_instruments", "Selected instrument collector drifted")
        elif collector_id == "fmp.company.short_descriptions":
            from quant_data.company.short_description_registry import collector_declaration
            if collector != collector_declaration(raw):
                raise _error(pointer, "short_descriptions", "Description collector drifted")
        elif collector_id == "fmp.company.statement_history":
            from quant_data.company.fmp_statement_registry import declaration
            if collector != declaration(raw):
                raise _error(pointer, "statement_history", "Statement history collector drifted")
        elif collector_id in ("sharadar.company.direct_sf1","sharadar.company.direct_definitions"):
            from quant_data.company.sharadar_direct_registry import declarations
            if collector != next(c for c in declarations(raw) if c["id"]==collector_id):
                raise _error(pointer,"sharadar_direct","Direct Sharadar collector drifted")
        elif collector_id == "nasdaq.company.sharadar_definitions":
            from quant_data.company.sharadar_definition_registry import declaration
            if collector != declaration(raw):
                raise _error(pointer,"sharadar_definitions","Sharadar definitions collector drifted")
        elif collector_id == "nasdaq.company.sharadar_sf1":
            from quant_data.company.sharadar_registry import COLLECTOR
            if collector != COLLECTOR:
                raise _error(pointer, "sharadar", "Sharadar collector drifted")
        elif collector_id == "local.market.collection_manifest":
            from quant_data.market.collection_registry import COLLECTOR
            if collector != COLLECTOR:
                raise _error(pointer, "collection_manifest", "Collection manifest collector drifted")
        elif collector_id == "openai.company.transcript_analysis":
            from quant_data.company.transcript_analysis_registry import COLLECTOR
            if collector != COLLECTOR:
                raise _error(pointer, "transcript_analysis", "Transcript analysis collector drifted")
        elif collector_id == "equibles.company.transcripts":
            from quant_data.company.equibles_registry import COLLECTOR
            if collector != COLLECTOR:
                raise _error(pointer, "equibles", "Equibles collector drifted")
        elif collector_id == "fmp.company.analyst_history":
            from quant_data.company.fmp_analyst_registry import COLLECTOR
            if collector != COLLECTOR:
                raise _error(pointer, "fmp_analyst", "FMP analyst collector drifted")
        elif collector_id in {"fmp.company.research_inputs", "fmp.market.research_gap_repair"}:
            from quant_data.company.fmp_research_registry import COLLECTORS
            if collector != next(c for c in COLLECTORS if c["id"] == collector_id):
                raise _error(pointer, "fmp_research", "FMP research collector drifted")
        elif collector_id in _COMPANY_MARKET_COLLECTOR_SPECS:
            if collector != _COMPANY_MARKET_COLLECTOR_SPECS[collector_id]:
                raise _error(pointer, "company_market_data", "Company market-data collector drifted")
        elif collector_id in _MACRO_DATABASE_EXPANSION_COLLECTOR_IDS:
            (
                expected_handler,
                expected_requests,
                expected_rows,
                expected_bytes,
                expected_seconds,
            ) = _MACRO_DATABASE_EXPANSION_COLLECTOR_SPECS[collector_id]
            if (
                collector["version"] != "1.0.0"
                or collector["handler"] != expected_handler
                or collector["network"] is not True
                or inputs
                or outputs != _MACRO_DATABASE_EXPANSION_DATASET_IDS
                or includes
                != (
                    "request_scope",
                    "normalization_version",
                    "normalized_observations",
                )
                or excludes
                != ("captured_at", "http_headers", "source_row_order")
                or mutation_policy
                != {
                    "mode": "append_versions_and_snapshot_membership",
                    "unchanged": "zero_persistent_writes",
                }
                or workload
                != {
                    "max_requests": expected_requests,
                    "max_rows": expected_rows,
                    "max_bytes": expected_bytes,
                    "max_seconds": expected_seconds,
                }
                or retry
                != {
                    "transient_classes": [],
                    "max_attempts": 1,
                    "backoff": "none_single_attempt",
                    "honor_retry_after": False,
                }
                or configuration_env
            ):
                raise _error(
                    pointer,
                    "macro_database_expansion",
                    "Macro database-expansion collector drifted",
                )
        elif collector_id == _FMP_STOCK_LATEST_COLLECTOR_ID:
            if (
                collector["handler"] != "news.fmp_stock_latest"
                or collector["network"] is not True
                or inputs
                or outputs
                != (
                    "news.fmp.stock_latest_evidence",
                    "news.fmp.stock_latest_articles",
                )
                or includes
                != (
                    "request_scope",
                    "normalization_version",
                    "normalized_partial_page",
                )
                or excludes
                != ("api_key", "captured_at", "http_headers", "source_row_order")
                or mutation_policy
                != {
                    "mode": "append_versions_and_capture_membership",
                    "unchanged": "zero_persistent_writes",
                }
                or workload
                != {
                    "max_requests": 1,
                    "max_rows": 1_000,
                    "max_bytes": 67_108_864,
                    "max_seconds": 60,
                }
                or retry
                != {
                    "transient_classes": [],
                    "max_attempts": 1,
                    "backoff": "none_single_attempt",
                    "honor_retry_after": False,
                }
                or configuration_env != ("FMP_API_KEY",)
            ):
                raise _error(
                    pointer,
                    "fmp_stock_latest",
                    "FMP stock-latest collector drifted",
                )
        elif collector_id == _FMP_STOCK_LATEST_CURRENT_COLLECTOR_ID:
            if (
                collector["version"] != "1.0.0"
                or collector["handler"] != "news.fmp_stock_latest_current"
                or collector["network"] is not True
                or inputs
                or outputs
                != (
                    "news.fmp.stock_latest_current_evidence",
                    "news.fmp.stock_latest_current_articles",
                )
                or includes
                != (
                    "request_scope",
                    "normalization_version",
                    "normalized_partial_page",
                )
                or excludes
                != ("api_key", "captured_at", "http_headers", "source_row_order")
                or mutation_policy
                != {
                    "mode": "append_versions_and_capture_membership",
                    "unchanged": "zero_persistent_writes",
                }
                or workload
                != {
                    "max_requests": 1,
                    "max_rows": 1_000,
                    "max_bytes": 67_108_864,
                    "max_seconds": 60,
                }
                or retry
                != {
                    "transient_classes": [],
                    "max_attempts": 1,
                    "backoff": "none_single_attempt",
                    "honor_retry_after": False,
                }
                or configuration_env != ("FMP_API_KEY",)
            ):
                raise _error(
                    pointer,
                    "fmp_stock_latest_current",
                    "FMP current stock-latest collector drifted",
                )
        elif collector_id == _CURRENT_MULTI_SOURCE_COLLECTOR_ID:
            if (
                collector["version"] != "1.0.0"
                or collector["handler"] != "news.current_multi_source"
                or collector["network"] is not True
                or inputs != ("market.stage10.instruments",)
                or outputs
                != (
                    "news.current_multi_source_evidence",
                    "news.current_multi_source_articles",
                )
                or includes
                != (
                    "request_scope",
                    "normalization_version",
                    "normalized_partial_feed",
                )
                or excludes
                != (
                    "fmp_api_key",
                    "alpaca_api_key",
                    "alpaca_api_secret",
                    "captured_at",
                    "http_headers",
                    "source_row_order",
                )
                or mutation_policy
                != {
                    "mode": "append_versions_and_capture_membership",
                    "unchanged": "zero_persistent_writes",
                }
                or workload
                != {
                    "max_requests": 24,
                    "max_rows": 1_000,
                    "max_bytes": 67_108_864,
                    "max_seconds": 60,
                }
                or retry
                != {
                    "transient_classes": [],
                    "max_attempts": 1,
                    "backoff": "none_single_attempt",
                    "honor_retry_after": False,
                }
                or configuration_env
                != (
                    "FMP_API_KEY",
                    "ALPACA_API_KEY",
                    "ALPACA_API_SECRET",
                )
            ):
                raise _error(
                    pointer,
                    "current_multi_source_news",
                    "Current multi-source news collector drifted",
                )
        elif collector_id == _STAGE12B_COLLECTOR_ID:
            if (
                collector["version"] != "1.0.0"
                or collector["handler"]
                != "market.stage12b_fmp_daily_incremental_fixture"
                or collector["network"] is not False
                or inputs != _STAGE12B_INPUT_DATASET_IDS
                or outputs != _STAGE12B_OUTPUT_DATASET_IDS
                or includes
                != (
                    "request_scope",
                    "normalization_version",
                    "normalized_complete_batch",
                )
                or excludes != ("captured_at", "source_row_order")
                or mutation_policy
                != {
                    "mode": "append_versions_and_move_current_projection",
                    "unchanged": "zero_persistent_writes",
                }
                or workload
                != {
                    "max_requests": 1,
                    "max_rows": 5,
                    "max_bytes": 65_536,
                    "max_seconds": 30,
                }
                or retry
                != {
                    "transient_classes": [],
                    "max_attempts": 1,
                    "backoff": "none",
                    "honor_retry_after": False,
                }
                or configuration_env
            ):
                raise _error(
                    pointer,
                    "stage12b_fixture",
                    "Stage 12B incremental fixture collector drifted",
                )
        elif collector_id == _STAGE12C_COLLECTOR_ID:
            if (
                collector["version"] != "1.0.0"
                or collector["handler"]
                != "market.stage12c_fmp_daily_incremental_manual"
                or collector["network"] is not True
                or inputs != _STAGE12C_INPUT_DATASET_IDS
                or outputs != _STAGE12C_OUTPUT_DATASET_IDS
                or includes
                != (
                    "scope_manifest_sha256",
                    "request_scope",
                    "normalization_version",
                    "normalized_complete_batch",
                )
                or excludes
                != ("api_key", "captured_at", "http_headers", "source_row_order")
                or mutation_policy
                != {
                    "mode": "append_versions_and_move_current_projection",
                    "unchanged": "zero_persistent_writes",
                }
                or workload
                != {
                    "max_requests": 1,
                    "max_rows": 2,
                    "max_bytes": 65_536,
                    "max_seconds": 45,
                }
                or retry
                != {
                    "transient_classes": [],
                    "max_attempts": 1,
                    "backoff": "none",
                    "honor_retry_after": False,
                }
                or configuration_env != ("FMP_API_KEY",)
            ):
                raise _error(
                    pointer,
                    "stage12c_live",
                    "Stage 12C manual collector drifted",
                )
        elif collector_id == _STAGE9_COLLECTOR_ID:
            if (
                collector["handler"] != "market.fmp_daily_price"
                or collector["network"] is not True
                or inputs
                or outputs
                != (
                    "market.fmp.daily_price_evidence",
                    "market.fmp.instruments",
                    "market.fmp.daily_prices",
                )
                or includes
                != (
                    "request_scope",
                    "normalization_version",
                    "normalized_complete_batch",
                )
                or excludes
                != ("api_key", "captured_at", "http_headers", "source_row_order")
                or workload
                != {
                    "max_requests": 1,
                    "max_rows": 22,
                    "max_bytes": 262_144,
                    "max_seconds": 30,
                }
                or retry
                != {
                    "transient_classes": ["http_429", "http_500", "transport"],
                    "max_attempts": 1,
                    "backoff": "none_single_attempt",
                    "honor_retry_after": False,
                }
                or configuration_env != ("FMP_API_KEY",)
            ):
                raise _error(pointer, "stage9_fmp", "Stage 9 FMP collector drifted")
        elif collector_id == _IWM_ETF_DAILY_HISTORY_BACKFILL_COLLECTOR_ID:
            if (
                collector["version"] != "1.0.0"
                or collector["handler"]
                != "market.fmp_iwm_etf_daily_history_backfill"
                or collector["network"] is not True
                or inputs
                != (
                    "market.stage10.instruments",
                    "market.stage10.universes",
                )
                or outputs != _IWM_ETF_DAILY_HISTORY_BACKFILL_DATASET_IDS
                or includes
                != (
                    "iwm_scope_manifest_sha256",
                    "backfill_scope_manifest_sha256",
                    "request_scope",
                    "normalization_version",
                    "normalized_complete_backfill_sha256",
                )
                or excludes
                != ("api_key", "captured_at", "http_headers", "source_row_order")
                or mutation_policy
                != {
                    "mode": "append_missing_price_versions",
                    "unchanged": "zero_persistent_writes",
                }
                or workload
                != {
                    "max_requests": 1,
                    "max_rows": 30_000,
                    "max_bytes": 16_777_216,
                    "max_seconds": 45,
                }
                or retry
                != {
                    "transient_classes": [],
                    "max_attempts": 1,
                    "backoff": "none_single_attempt",
                    "honor_retry_after": False,
                }
                or configuration_env != ("FMP_API_KEY",)
            ):
                raise _error(
                    pointer,
                    "iwm_etf_daily_history_backfill",
                    "IWM ETF daily-history backfill collector drifted",
                )
        elif collector_id == _IWM_ETF_DAILY_HISTORY_COLLECTOR_ID:
            if (
                collector["version"] != "1.0.0"
                or collector["handler"] != "market.fmp_iwm_etf_daily_history"
                or collector["network"] is not True
                or inputs != (
                    "market.stage10.instruments",
                    "market.stage10.universes",
                )
                or outputs != _IWM_ETF_DAILY_HISTORY_DATASET_IDS
                or includes
                != (
                    "scope_manifest_sha256",
                    "base_scope_manifest_sha256",
                    "successor_membership_sha256",
                    "request_scope",
                    "normalization_version",
                    "normalized_complete_history_sha256",
                )
                or excludes
                != ("api_key", "captured_at", "http_headers", "source_row_order")
                or mutation_policy
                != {
                    "mode": "append_successor_universe_and_price_versions",
                    "unchanged": "zero_persistent_writes",
                }
                or workload
                != {
                    "max_requests": 1,
                    "max_rows": 30_000,
                    "max_bytes": 16_777_216,
                    "max_seconds": 45,
                }
                or retry
                != {
                    "transient_classes": [],
                    "max_attempts": 1,
                    "backoff": "none_single_attempt",
                    "honor_retry_after": False,
                }
                or configuration_env != ("FMP_API_KEY",)
            ):
                raise _error(
                    pointer,
                    "iwm_etf_daily_history",
                    "IWM ETF daily-history collector drifted",
                )
        elif collector_id in _STAGE10_COLLECTOR_IDS:
            universe_capture = collector_id == "fmp.market.stage10_universe_capture"
            expected_inputs = (
                ()
                if universe_capture
                else (
                    "market.stage10.instruments",
                    "market.stage10.universes",
                )
            )
            expected_outputs = (
                (
                    "market.stage10.source_evidence",
                    "market.stage10.instruments",
                    "market.stage10.universes",
                )
                if universe_capture
                else (
                    "market.stage10.source_evidence",
                    "market.stage10.daily_prices",
                )
            )
            expected_includes = (
                (
                    "scope_manifest_sha256",
                    "request_scope",
                    "normalization_version",
                    "normalized_complete_snapshot",
                )
                if universe_capture
                else (
                    "scope_manifest_sha256",
                    "request_scope",
                    "normalization_version",
                    "normalized_complete_history",
                )
            )
            expected_workload = (
                {
                    "max_requests": 3,
                    "max_rows": 600,
                    "max_bytes": 4_194_304,
                    "max_seconds": 135,
                }
                if universe_capture
                else {
                    "max_requests": 1,
                    "max_rows": 30_000,
                    "max_bytes": 16_777_216,
                    "max_seconds": 45,
                }
            )
            if (
                collector["handler"]
                != (
                    "market.stage10_fmp_universes"
                    if universe_capture
                    else "market.stage10_fmp_daily_history"
                )
                or collector["network"] is not True
                or inputs != expected_inputs
                or outputs != expected_outputs
                or includes != expected_includes
                or excludes
                != ("api_key", "captured_at", "http_headers", "source_row_order")
                or mutation_policy
                != {
                    "mode": (
                        "append_complete_universe_snapshots"
                        if universe_capture
                        else "append_versions_and_move_current_projection"
                    ),
                    "unchanged": "zero_persistent_writes",
                }
                or workload != expected_workload
                or retry
                != {
                    "transient_classes": ["http_429", "http_500", "transport"],
                    "max_attempts": 1,
                    "backoff": "none_operator_resume",
                    "honor_retry_after": False,
                }
                or configuration_env != ("FMP_API_KEY",)
            ):
                raise _error(
                    pointer,
                    "stage10_fmp",
                    "Stage 10 FMP collector drifted",
                )
        elif collector_id in _STAGE11_COLLECTOR_IDS:
            expected = {
                "bea.macro.stage11_nipa_history": {
                    "handler": "macro.stage11_bea_nipa_history",
                    "outputs": (
                        "macro.bea.nipa_history_evidence",
                        "macro.bea.nipa_history",
                    ),
                    "configuration_env": ("BEA_API_KEY",),
                    "workload": {
                        "max_requests": 2,
                        "max_rows": 2_000,
                        "max_bytes": 8_388_608,
                        "max_seconds": 90,
                    },
                },
                "eia.macro.stage11_electricity_retail_history": {
                    "handler": "macro.stage11_eia_retail_history",
                    "outputs": (
                        "macro.eia.electricity_retail_history_evidence",
                        "macro.eia.electricity_retail_history",
                    ),
                    "configuration_env": ("EIA_API_KEY",),
                    "workload": {
                        "max_requests": 8,
                        "max_rows": 40_000,
                        "max_bytes": 16_777_216,
                        "max_seconds": 360,
                    },
                },
                "eia.macro.stage11_petroleum_weekly_stock_history": {
                    "handler": "macro.stage11_eia_weekly_history",
                    "outputs": (
                        "macro.eia.petroleum_weekly_stock_history_evidence",
                        "macro.eia.petroleum_weekly_stock_history",
                    ),
                    "configuration_env": ("EIA_API_KEY",),
                    "workload": {
                        "max_requests": 1,
                        "max_rows": 5_000,
                        "max_bytes": 16_777_216,
                        "max_seconds": 45,
                    },
                },
            }[collector_id]
            if (
                collector["handler"] != expected["handler"]
                or collector["network"] is not True
                or inputs
                or outputs != expected["outputs"]
                or includes
                != ("request_scope", "normalization_version", "normalized_complete_history")
                or excludes
                != ("api_key", "captured_at", "http_headers", "source_row_order")
                or mutation_policy
                != {
                    "mode": "append_versions_and_move_current_projection",
                    "unchanged": "zero_persistent_writes",
                }
                or workload != expected["workload"]
                or retry != _STAGE11_CANONICAL_RETRY_POLICY
                or configuration_env != expected["configuration_env"]
            ):
                raise _error(pointer, "stage11_macro", "Stage 11 macro collector drifted")
        elif collector_id in (
            _MACRO_VINTAGE_COLLECTOR_IDS
            | _EMPLOYMENT_VINTAGE_COLLECTOR_IDS
            | _MACRO_HISTORY_COLLECTOR_IDS
        ):
            expected = {
                "bea.macro.live_gdp_vintages": {
                    "handler": "macro.live_gdp_vintages",
                    "workload": {
                        "max_requests": 1,
                        "max_rows": 5_000,
                        "max_bytes": 1_048_576,
                        "max_seconds": 60,
                    },
                },
                "bls.macro.live_cpi_vintages": {
                    "handler": "macro.live_cpi_vintages",
                    "workload": {
                        "max_requests": 15,
                        "max_rows": 10_000,
                        "max_bytes": 8_388_608,
                        "max_seconds": 300,
                    },
                },
                "philadelphia_fed.macro.live_employment_vintages": {
                    "handler": "macro.live_employment_vintages",
                    "workload": {
                        "max_requests": 2,
                        "max_rows": 500_000,
                        "max_bytes": 16_777_216,
                        "max_seconds": 600,
                    },
                },
                "bls.macro.live_employment_current": {
                    "handler": "macro.live_employment_current",
                    "workload": {
                        "max_requests": 1,
                        "max_rows": 300,
                        "max_bytes": 2_097_152,
                        "max_seconds": 60,
                    },
                },
                "bls.macro.cpi_current_history": {
                    "handler": "macro.cpi_current_history",
                    "workload": {
                        "max_requests": 7,
                        "max_rows": 1_344,
                        "max_bytes": 2_097_152,
                        "max_seconds": 300,
                    },
                },
                "philadelphia_fed.macro.gdp_cpi_vintage_history": {
                    "handler": "macro.gdp_cpi_vintage_history",
                    "workload": {
                        "max_requests": 4,
                        "max_rows": 10_000,
                        "max_bytes": 8_388_608,
                        "max_seconds": 300,
                    },
                },
            }[collector_id]
            if (
                collector["handler"] != expected["handler"]
                or collector["network"] is not True
                or inputs
                or outputs
                != (
                    "macro.official_vintages_evidence",
                    "macro.official_vintages",
                )
                or includes
                != (
                    "request_scope",
                    "source_vintage",
                    "normalization_version",
                    "normalized_vintage_batch",
                )
                or excludes != ("captured_at", "http_headers", "source_row_order")
                or mutation_policy
                != {
                    "mode": "append_vintages_and_move_current_projection",
                    "unchanged": "zero_persistent_writes",
                }
                or workload != expected["workload"]
                or retry
                != {
                    "transient_classes": [],
                    "max_attempts": 1,
                    "backoff": "none",
                    "honor_retry_after": False,
                }
                or configuration_env
            ):
                raise _error(
                    pointer,
                    "macro_vintages",
                    "Live GDP/CPI vintage collector drifted",
                )
        elif collector_id == _ALPACA_SPY_OPTION_COLLECTOR_ID:
            if (
                collector["version"] != "1.0.0"
                or collector["handler"] != "market.alpaca_spy_option_surface"
                or collector["network"] is not True
                or inputs != ("market.stage10.instruments",)
                or outputs
                != _ALPACA_SPY_OPTION_DATASET_IDS
                + (_OPTION_RAW_EVIDENCE_DATASET_ID,)
                or includes
                != (
                    "request_scope",
                    "normalization_version",
                    "resolved_feed",
                    "environment",
                    "normalized_contracts",
                    "normalized_surface",
                    "synchronized_inputs",
                    "raw_response_content_sha256",
                )
                or excludes
                != (
                    "api_key",
                    "api_secret",
                    "captured_at",
                    "http_headers",
                    "raw_response_order",
                    "source_row_order",
                )
                or mutation_policy
                != {
                    "mode": "append_capture_cohort_and_bridge_instrument",
                    "unchanged": "zero_persistent_writes",
                }
                or workload
                != {
                    "max_requests": 4,
                    "max_rows": 10_000,
                    "max_bytes": 8_388_608,
                    "max_seconds": 120,
                }
                or retry
                != {
                    "transient_classes": [],
                    "max_attempts": 1,
                    "backoff": "none_single_attempt",
                    "honor_retry_after": False,
                }
                or configuration_env
                != ("ALPACA_API_KEY", "ALPACA_API_SECRET")
            ):
                raise _error(
                    pointer,
                    "alpaca_spy_options",
                    "Alpaca SPY option-surface collector drifted",
                )
        elif collector_id == _ALPACA_ETF_OPTION_COLLECTOR_ID:
            if (
                collector["version"] != "1.0.0"
                or collector["handler"] != "market.alpaca_etf_option_surface_grid"
                or collector["network"] is not True
                or inputs != ("market.stage10.instruments",)
                or outputs
                != _ALPACA_ETF_OPTION_DATASET_IDS
                + (_OPTION_RAW_EVIDENCE_DATASET_ID,)
                or includes
                != (
                    "request_scope",
                    "normalization_version",
                    "resolved_feed",
                    "environment",
                    "normalized_contracts",
                    "normalized_surface",
                    "synchronized_inputs",
                    "raw_response_content_sha256",
                )
                or excludes
                != (
                    "api_key",
                    "api_secret",
                    "captured_at",
                    "http_headers",
                    "raw_response_order",
                    "source_row_order",
                )
                or mutation_policy
                != {
                    "mode": "append_capture_cohort_and_bridge_instrument",
                    "unchanged": "zero_persistent_writes",
                }
                or workload
                != {
                    "max_requests": 362,
                    "max_rows": 900_016,
                    "max_bytes": 268_435_456,
                    "max_seconds": 900,
                }
                or retry
                != {
                    "transient_classes": [],
                    "max_attempts": 1,
                    "backoff": "none_single_attempt",
                    "honor_retry_after": False,
                }
                or configuration_env
                != ("ALPACA_API_KEY", "ALPACA_API_SECRET")
            ):
                raise _error(
                    pointer,
                    "alpaca_etf_options",
                    "Alpaca ETF option-surface grid collector drifted",
                )
        elif collector_id == _SEC_AAPL_COMPANYFACTS_COLLECTOR_ID:
            if (
                collector["version"] != "1.0.0"
                or collector["handler"] != "company.sec_aapl_fundamentals"
                or collector["network"] is not True
                or inputs
                or outputs != _SEC_AAPL_COMPANYFACTS_DATASET_IDS
                or includes
                != (
                    "request_scope",
                    "cik_scope",
                    "normalization_version",
                    "submissions",
                    "companyfacts",
                    "metric_mappings",
                )
                or excludes
                != (
                    "captured_at",
                    "http_headers",
                    "raw_response_order",
                    "source_row_order",
                    "user_agent_email",
                    "user_agent_name",
                )
                or mutation_policy
                != {
                    "mode": "append_sec_versions_and_membership",
                    "unchanged": "zero_persistent_writes",
                }
                or workload
                != {
                    "max_requests": 2,
                    "max_rows": 10_000,
                    "max_bytes": 16_777_216,
                    "max_seconds": 120,
                }
                or retry
                != {
                    "transient_classes": [],
                    "max_attempts": 1,
                    "backoff": "none_single_attempt",
                    "honor_retry_after": False,
                }
                or configuration_env
                != ("SEC_USER_AGENT_NAME", "SEC_USER_AGENT_EMAIL")
            ):
                raise _error(
                    pointer,
                    "sec_aapl_companyfacts",
                    "SEC AAPL company-fundamentals collector drifted",
                )
        elif collector_id == _SEC_MARKET_COMPANYFACTS_COLLECTOR_ID:
            if (
                collector["version"] != "1.0.0"
                or collector["handler"] != "company.sec_market_fundamentals"
                or collector["network"] is not True
                or inputs != ("market.stage10.instruments",)
                or outputs != _SEC_MARKET_COMPANYFACTS_DATASET_IDS
                or includes
                != (
                    "request_scope",
                    "cik_scope",
                    "normalization_version",
                    "submissions",
                    "companyfacts",
                    "metric_mappings",
                )
                or excludes
                != (
                    "captured_at",
                    "http_headers",
                    "raw_response_order",
                    "source_row_order",
                    "user_agent_email",
                    "user_agent_name",
                )
                or mutation_policy
                != {
                    "mode": "append_sec_versions_and_membership",
                    "unchanged": "zero_persistent_writes",
                }
                or workload
                != {
                    "max_requests": 2,
                    "max_rows": 50_000,
                    "max_bytes": 67_108_864,
                    "max_seconds": 120,
                }
                or retry
                != {
                    "transient_classes": [],
                    "max_attempts": 1,
                    "backoff": "none_single_attempt",
                    "honor_retry_after": False,
                }
                or configuration_env
                != ("SEC_USER_AGENT_NAME", "SEC_USER_AGENT_EMAIL")
            ):
                raise _error(
                    pointer,
                    "sec_market_companyfacts",
                    "SEC market-universe company-fundamentals collector drifted",
                )
        elif collector["network"] is not False or any(
            not name.startswith("QUANT_") for name in configuration_env
        ):
            raise _error(
                f"{pointer}/configuration_env", "environment", "Unsafe configuration name"
            )
        if (
            collector["physical_locks"] != "derived_from_output_store_paths"
            or collector["schedule_eligibility"] != {"mode": "manual_only"}
        ):
            raise _error(pointer, "routing", "Collector routing must be registry-derived")

    jobs = _validate_stage7_jobs(
        raw["jobs"],
        collectors=collectors,
        dataset_store_by_id=dataset_store_by_id,
        expected_roles=expected_roles,
    )

    dashboard = tuple(raw["dashboard"])
    dashboard_ids: set[str] = set()
    dashboard_routes: set[str] = set()
    dashboard_api_routes: set[str] = set()
    for index, exposure in enumerate(dashboard):
        pointer = f"/dashboard/{index}"
        if not isinstance(exposure, dict):
            raise _error(pointer, "type", "Dashboard exposure must be an object")
        _require_keys(
            exposure,
            {
                "id",
                "route",
                "visibility",
                "datasets",
                "tools",
                "relations",
                "filters",
                "sort_fields",
                "pagination",
                "api_routes",
            },
            pointer,
        )
        dashboard_id = _stable_identifier(exposure["id"], f"{pointer}/id")
        dashboard_datasets = _stable_identifier_array(
            exposure["datasets"], f"{pointer}/datasets"
        )
        dashboard_tools = _stable_identifier_array(
            exposure["tools"], f"{pointer}/tools"
        )
        expected = _STAGE6_DASHBOARD_EXPECTATIONS.get(dashboard_id)
        relations = exposure["relations"]
        api_routes = exposure["api_routes"]
        filters = exposure["filters"]
        sort_fields = exposure["sort_fields"]
        pagination = exposure["pagination"]
        if (
            expected is None
            or dashboard_id in dashboard_ids
            or exposure["route"] != _STAGE6_DASHBOARD_ROUTES[dashboard_id]
            or exposure["route"] in dashboard_routes
            or exposure["visibility"] != "local_private"
            or not set(dashboard_datasets).issubset(dataset_ids)
            or not set(dashboard_tools).issubset(tool_names)
            or frozenset(dashboard_datasets) != expected["datasets"]
            or tuple(dashboard_tools) != tuple(expected["tools"])
            or not isinstance(relations, list)
            or tuple(relations) != tuple(expected["relations"])
            or not all(
                isinstance(item, str) and _IDENTIFIER.fullmatch(item)
                for item in relations
            )
            or not isinstance(api_routes, list)
            or tuple(api_routes) != tuple(expected["api_routes"])
            or not all(
                isinstance(item, str) and item.startswith("/api/")
                for item in api_routes
            )
            or any(item in dashboard_api_routes for item in api_routes)
        ):
            raise _error(pointer, "reference", "Dashboard exposure reference is invalid")
        if not isinstance(filters, dict):
            raise _error(f"{pointer}/filters", "type", "Dashboard filters must be a schema")
        _validate_strict_schema(filters, f"{pointer}/filters")
        properties = filters.get("properties", {})
        if (
            filters.get("type") != "object"
            or filters.get("additionalProperties") is not False
            or not isinstance(properties, dict)
            or set(properties).intersection(_PROHIBITED_DASHBOARD_KEYS)
        ):
            raise _error(
                f"{pointer}/filters",
                "schema",
                "Dashboard filters must be closed and path-free",
            )
        if (
            not isinstance(sort_fields, list)
            or len(sort_fields) != len(set(sort_fields))
            or not all(
                isinstance(item, str) and _IDENTIFIER.fullmatch(item)
                for item in sort_fields
            )
        ):
            raise _error(
                f"{pointer}/sort_fields",
                "allowlist",
                "Dashboard sort fields must be a finite identifier allowlist",
            )
        if not isinstance(pagination, dict):
            raise _error(f"{pointer}/pagination", "type", "Pagination must be an object")
        _require_keys(
            pagination,
            {"default_limit", "max_limit"},
            f"{pointer}/pagination",
        )
        default_limit = pagination["default_limit"]
        max_limit = pagination["max_limit"]
        if (
            isinstance(default_limit, bool)
            or not isinstance(default_limit, int)
            or isinstance(max_limit, bool)
            or not isinstance(max_limit, int)
            or not 1 <= default_limit <= max_limit <= 100
        ):
            raise _error(
                f"{pointer}/pagination",
                "bounds",
                "Dashboard pagination bounds are invalid",
            )
        query_contract_sha256 = hashlib.sha256(
            dumps_strict(
                {
                    "filters": filters,
                    "sort_fields": sort_fields,
                    "pagination": pagination,
                }
            ).encode("utf-8")
        ).hexdigest()
        if query_contract_sha256 != expected["query_contract_sha256"]:
            raise _error(
                pointer,
                "query_contract",
                "Dashboard filter, sort, or pagination contract changed",
            )
        dashboard_ids.add(dashboard_id)
        dashboard_routes.add(exposure["route"])
        dashboard_api_routes.update(api_routes)
        allowed_relations = {
            relation
            for dataset in datasets
            if dataset.id in exposure["datasets"]
            for relation in dataset.relations
        }
        if not set(relations).issubset(allowed_relations):
            raise _error(
                f"{pointer}/relations",
                "ownership",
                "Dashboard relation is not owned by a declared dataset",
            )
    if tuple(exposure["id"] for exposure in dashboard) != tuple(
        _STAGE6_DASHBOARD_ROUTES
    ):
        raise RegistryError("Dashboard presentation order does not match Stage 6")

    exports = _validate_stage8_exports(
        raw["exports"],
        datasets=tuple(datasets),
        owned_relations=owned_relations,
    )
    export_dataset_refs = {
        dataset_id: {
            declaration.id
            for declaration in exports
            if dataset_id in declaration.datasets
        }
        for dataset_id in dataset_ids
    }

    all_tool_contracts = [
        *tools,
        *[
            variant
            for policy in tool_version_policies
            for variant in policy["variants"]
        ],
    ]
    tool_dataset_refs = {
        dataset_id: {
            tool["id"] for tool in all_tool_contracts if dataset_id in tool["datasets"]
        }
        for dataset_id in dataset_ids
    }
    collector_dataset_refs = {
        dataset_id: {
            collector["id"]
            for collector in collectors
            if dataset_id in collector["output_datasets"]
        }
        for dataset_id in dataset_ids
    }
    dashboard_dataset_refs = {
        dataset_id: {
            exposure["id"]
            for exposure in dashboard
            if dataset_id in exposure["datasets"]
        }
        for dataset_id in dataset_ids
    }
    for dataset in datasets:
        if (
            set(dataset.collector_ids) != collector_dataset_refs[dataset.id]
            or set(dataset.tool_ids) != tool_dataset_refs[dataset.id]
            or set(dataset.dashboard_ids) != dashboard_dataset_refs[dataset.id]
            or set(dataset.export_ids) != export_dataset_refs[dataset.id]
        ):
            raise _error(
                f"/datasets/{dataset.id}",
                "reciprocal_reference",
                "Dataset producer/consumer references are not reciprocal",
            )

    presentation = raw["presentation_order"]
    if not isinstance(presentation, dict):
        raise RegistryError("Presentation order must be an object")
    _require_keys(presentation, {"stores", "tools"}, "/presentation_order")
    presentation_tools = _stable_identifier_array(
        presentation["tools"], "/presentation_order/tools", allow_empty=False
    )
    if presentation["stores"] != [role.value for role in STORE_ROLES] or tuple(
        presentation_tools
    ) != CURRENT_PUBLIC_TOOL_NAMES:
        raise RegistryError("Presentation order must match the reviewed active inventory")

    return Registry(
        schema_id=raw["schema_id"],
        schema_version=raw["schema_version"],
        registry_version=raw["registry_version"],
        status=raw["status"],
        project_root=root,
        source_path=source,
        stores=tuple(stores),
        migrations=tuple(migrations),
        datasets=tuple(datasets),
        collectors=collectors,
        jobs=jobs,
        exports=exports,
        tools=tuple(tools),
        tool_version_policies=tool_version_policies,
        dashboard=dashboard,
        raw=raw,
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
    )


# Preserve existing imports without loading historical projection code on startup.
_COMPATIBILITY_EXPORTS = frozenset(
    (
        '_stage1_dashboard_projection',
        '_legacy_tool_projection',
        '_export_free_dataset_projection',
        '_tool_policy_variant_inventory',
        '_REGISTRY_259_ADDITIVE_VARIANTS',
        '_pre_website_policies',
        '_pre_registry_267_policies',
        '_pre_registry_266_policies',
        '_WEBSITE_SOURCE_REGISTRY_SHA256',
        '_ETF_SNAPSHOT_V2_PREDECESSOR_SHA256',
        '_ETF_SNAPSHOT_V2_PREDECESSOR_CATALOG_SHA256',
        'transcript_research_registry_profile',
        'forward_pe_analysis_registry_profile',
        'forward_pe_tools_registry_profile',
        'sharadar_company_tools_registry_profile',
        'transcript_tools_registry_profile',
        'price_basis_registry_profile',
        'etf_snapshot_v2_registry_profile',
        'structured_transcript_registry_profile',
        'sharadar_direct_registry_profile',
        'fmp_research_lookup_registry_profile',
        'sec_completion_registry_profile',
        'sharadar_definitions_registry_profile',
        'statement_history_registry_profile',
        'sharadar_registry_profile',
        'collection_universe_registry_profile',
        'transcript_analysis_registry_profile',
        'equibles_transcript_registry_profile',
        'analyst_history_registry_profile',
        'website_source_registry_profile',
        '_FMP_RESEARCH_REGISTRY_SOURCE_SHA256',
        'fmp_research_registry_profile',
        'etf_snapshot_registry_profile',
        'company_market_data_registry_profile',
        'macro_database_expansion_registry_profile',
        'data_status_options_registry_profile',
        '_pre_registry_264_policies',
        'news_research_registry_profile',
        '_pre_registry_262_policies',
        '_pre_registry_261_policies',
        '_pre_registry_260_policies',
        '_pre_registry_259_policies',
        '_pre_technical_indicators_v23_policies',
        '_pre_investment_analysis_policies',
        'option_raw_evidence_registry_profile',
        'multi_source_current_news_registry_profile',
        'current_news_registry_profile',
        'technical_indicators_v27_registry_profile',
        'technical_indicators_v26_registry_profile',
        'technical_indicators_v25_registry_profile',
        'registry_259_additive_successors_profile',
        'technical_indicators_v23_registry_profile',
        'investment_analysis_v2_registry_profile',
        'technical_indicators_v22_registry_profile',
        'technical_indicators_v21_registry_profile',
        'placeholder_successor_registry_profile',
        'company_filing_pagination_registry_profile',
        'stage10_research_analytics_registry_profile',
        'fmp_iwm_etf_daily_history_backfill_registry_profile',
        'fmp_iwm_etf_daily_history_registry_profile',
        'alpaca_etf_options_registry_profile',
        'technical_indicators_v2_registry_profile',
        'analytics_foundation_registry_profile',
        'canonical_access_registry_profile',
        'sec_market_companyfacts_registry_profile',
        'sec_aapl_companyfacts_registry_profile',
        'alpaca_spy_options_registry_profile',
        'market_available_ticker_v1_registry_profile',
        'market_price_series_v1_registry_profile',
        'fmp_calendar_incremental_registry_profile',
        'econometrics_model_suite_v3_registry_profile',
        'structural_breaks_v2_registry_profile',
        'stationarity_v21_registry_profile',
        'robust_econometrics_v21_registry_profile',
        'bea_personal_income_registry_profile',
        'econometrics_v2_registry_profile',
        'timeseries_analysis_v2_registry_profile',
        'market_return_v2_registry_profile',
        'gdi_vintage_registry_profile',
        'bls_price_wage_productivity_registry_profile',
        'official_macro_extension_registry_profile',
        'nyfed_cmdi_registry_profile',
        'official_conditions_registry_profile',
        'nyfed_soma_summary_registry_profile',
        'nyfed_repo_facilities_registry_profile',
        'nyfed_overnight_rates_registry_profile',
        'fmp_treasury_yield_curve_registry_profile',
        'fmp_wholesale_calendar_registry_profile',
        'fmp_employment_release_calendar_registry_profile',
        'fmp_gdp_cpi_release_calendar_registry_profile',
        'macro_history_extension_registry_profile',
        'employment_vintage_registry_profile',
        'macro_vintage_registry_profile',
        'stage12d_registry_profile',
        'stage12c_registry_profile',
        'stage12b_registry_profile',
        'stage12_registry_profile',
        'stage11_registry_profile',
        'stage10_registry_profile',
        '_stage10_input',
        'stage9_registry_profile',
        '_stage9_input',
        'stage8_registry_profile',
        '_stage8_input',
        'stage2_registry_profile',
        'stage3_registry_profile',
        'stage4_registry_profile',
        'stage5_registry_profile',
        'stage6_registry_profile',
        'stage7_registry_profile',
    )
)

# Preserve the former star-import surface, including historical public profiles.
__all__ = sorted(
    {name for name in globals() if not name.startswith("_")}
    | {name for name in _COMPATIBILITY_EXPORTS if not name.startswith("_")}
)


def __getattr__(name: str) -> Any:
    if name in _COMPATIBILITY_EXPORTS:
        from . import registry_compat

        return getattr(registry_compat, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | _COMPATIBILITY_EXPORTS)
