"""Loopback-only read inspector for canonical data and current public tools.

This is intentionally separate from the frozen Stage 6 portal.  Browser input
selects only fixed, bounded read templates; it can never select a database
path, relation, SQL statement, provider, or write operation.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import os
import re
import sqlite3
import sys
import stat
from collections.abc import Sequence
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from http import HTTPStatus
from pathlib import Path
from typing import Any, Iterator, Mapping
from urllib.parse import urlencode, urlsplit

from .boundary.application import (
    HttpResponse,
    MethodNotAllowedError,
    RouteNotFoundError,
    Stage1Application,
    create_server,
)
from .dashboard.application import INTER_FONT_SHA256
from .dashboard.current_tool_page import (
    latest_tool_version,
    render_current_agent_tools_page,
)
from .dashboard.data_status_page import render_data_status_page
from .errors import (
    Issue,
    QuantDataError,
    ResourceLimitError,
    StoreUnavailableError,
    ValidationError,
)
from .json_codec import dumps_strict, loads_strict
from .macro.fmp_calendar_wholesale import parse_fmp_us_calendar_wholesale
from .macro.fmp_release_surprises import (
    ALL_SURPRISE_KINDS,
    GDP_ADVANCE_KIND,
    MacroReleaseSurpriseRepository,
)
from .macro.fmp_treasury_curve import CURVE_VARIANT, PROVIDER, TENOR_MANIFEST
from .macro.official_conditions import (
    BEA_PERSONAL_INCOME_MANIFEST,
    BIS_MANIFEST,
    BLS_PRICE_WAGE_PRODUCTIVITY_MANIFEST,
    CFNAI_MANIFEST,
    CHICAGO_MANIFEST,
    CMDI_MANIFEST,
    EIA_GAS_MANIFEST,
    EIA_PETROLEUM_FUNDAMENTALS_MANIFEST,
    H41_MANIFEST,
    FED_POLICY_RATE_MANIFEST,
    INDUSTRIAL_PRODUCTION_MANIFEST,
    NBER_RECESSION_MANIFEST,
    TREASURY_DEBT_MANIFEST,
    TREASURY_FISCAL_BALANCE_MANIFEST,
    TREASURY_TGA_MANIFEST,
)
from .macro.nyfed_overnight_rates import (
    PROVIDER as NYFED_PROVIDER,
    RATE_MANIFEST,
)
from .macro.nyfed_repo_facilities import (
    FACILITY_MANIFEST,
    PROVIDER as NYFED_REPO_PROVIDER,
)
from .macro.nyfed_soma_summary import COMPONENT_MANIFEST as SOMA_COMPONENT_MANIFEST
from .macro.stage11_eia import EIA_WEEKLY_CANONICAL_SERIES_ID
from .market.alpaca_options import (
    ALPACA_ETF_OPTIONS_DTE_TARGETS as _OPTIONS_SURFACE_TARGET_DTES,
    ALPACA_ETF_OPTIONS_UNIVERSE as _OPTIONS_SURFACE_UNDERLYINGS,
)
from .news.tool_repository import CURRENT_NEWS_TOOL_SOURCE_IDS
from .registry import CANONICAL_REGISTRY_PATH, Registry, load_registry
from .stores import StoreMap, StoreRole, read_connection, resolve_store_map, stable_id


_CURRENT_REGISTRY = "2.68.0"
_CURRENT_SCHEMA = "1.9.0"
_ASSET_ROOT = Path(__file__).with_name("dashboard") / "static"
_VIEWS = (
    "market-prices",
    "market-instruments",
    "spy-options",
    "options-surfaces",
    "company-fundamentals",
    "macro-current",
    "macro-vintages",
    "macro-surprises",
    "price-wage-productivity",
    "personal-income-outlays",
    "treasury-curve",
    "overnight-rates",
    "policy-rates",
    "industrial-production",
    "repo-facilities",
    "soma-summary",
    "h41-liquidity",
    "treasury-cash",
    "treasury-debt",
    "fiscal-balance",
    "financial-conditions",
    "national-activity",
    "bis-credit",
    "credit-market-distress",
    "natural-gas-storage",
    "crude-oil-stocks",
    "petroleum-fundamentals",
    "electricity-retail",
    "recession-chronology",
    "fmp-economic-calendar",
)
_VIEW_LABELS = {
    "market-prices": "Market prices",
    "market-instruments": "Market symbols",
    "spy-options": "SPY options",
    "options-surfaces": "Options surfaces",
    "company-fundamentals": "Company fundamentals",
    "macro-current": "Macro current",
    "macro-vintages": "Macro vintages",
    "macro-surprises": "Release surprises",
    "price-wage-productivity": "Prices, wages & productivity",
    "personal-income-outlays": "Personal income & outlays",
    "treasury-curve": "Treasury curve",
    "overnight-rates": "Overnight rates",
    "policy-rates": "Policy rates",
    "industrial-production": "Industrial production",
    "repo-facilities": "Repo facilities",
    "soma-summary": "SOMA summary",
    "h41-liquidity": "Fed H.4.1 liquidity",
    "treasury-cash": "Treasury cash balance",
    "treasury-debt": "Treasury debt",
    "fiscal-balance": "Federal fiscal balance",
    "financial-conditions": "Financial conditions",
    "national-activity": "National activity",
    "bis-credit": "BIS credit conditions",
    "credit-market-distress": "Corporate bond distress",
    "natural-gas-storage": "Natural gas storage",
    "crude-oil-stocks": "Crude oil stocks",
    "petroleum-fundamentals": "Petroleum fundamentals",
    "electricity-retail": "Electricity retail",
    "recession-chronology": "Recession chronology",
    "fmp-economic-calendar": "Raw FMP calendar",
}
_TREASURY_TENORS = tuple(item.tenor for item in TENOR_MANIFEST)
_OVERNIGHT_RATE_CODES = tuple(item.code for item in RATE_MANIFEST)
_EIA_RETAIL_SERIES = (
    ("macro.eia.electricity.retail_sales", "Electricity retail sales"),
    ("macro.eia.electricity.retail_revenue", "Electricity retail revenue"),
    ("macro.eia.electricity.retail_price", "Electricity retail price"),
    ("macro.eia.electricity.retail_customers", "Electricity retail customers"),
)
_EIA_RETAIL_SERIES_IDS = tuple(item[0] for item in _EIA_RETAIL_SERIES)
_REPO_FACILITY_CODES = tuple(item.code for item in FACILITY_MANIFEST)
_SOMA_COMPONENTS = tuple(item.category for item in SOMA_COMPONENT_MANIFEST)
_OFFICIAL_VIEW_MANIFESTS = {
    "personal-income-outlays": (
        "bea",
        BEA_PERSONAL_INCOME_MANIFEST,
    ),
    "price-wage-productivity": (
        "bls",
        BLS_PRICE_WAGE_PRODUCTIVITY_MANIFEST,
    ),
    "policy-rates": ("federal_reserve_policy", FED_POLICY_RATE_MANIFEST),
    "industrial-production": (
        "federal_reserve_industrial_production",
        INDUSTRIAL_PRODUCTION_MANIFEST,
    ),
    "h41-liquidity": ("federal_reserve_h41", H41_MANIFEST),
    "treasury-cash": ("treasury_fiscal_data", TREASURY_TGA_MANIFEST),
    "treasury-debt": ("treasury_fiscal_data", TREASURY_DEBT_MANIFEST),
    "fiscal-balance": (
        "treasury_fiscal_data",
        TREASURY_FISCAL_BALANCE_MANIFEST,
    ),
    "financial-conditions": ("chicagofed", CHICAGO_MANIFEST),
    "national-activity": ("chicagofed", CFNAI_MANIFEST),
    "bis-credit": ("bis", BIS_MANIFEST),
    "credit-market-distress": ("nyfed_cmdi", CMDI_MANIFEST),
    "natural-gas-storage": ("eia", EIA_GAS_MANIFEST),
    "petroleum-fundamentals": (
        "eia",
        EIA_PETROLEUM_FUNDAMENTALS_MANIFEST,
    ),
    "recession-chronology": ("nber", NBER_RECESSION_MANIFEST),
}
_MACRO_SERIES = (
    "macro.gdp.real_qoq_saar_pct",
    "macro.gdp.nominal_billions",
    "macro.gdi.real_qoq_saar_pct",
    "macro.gdi.nominal_billions",
    "macro.bls.cpi_u_all_items_sa",
    "macro.bls.cpi_u_core_sa",
    "macro.bls.total_nonfarm_payrolls_sa",
    "macro.bls.unemployment_rate_sa",
    "macro.philadelphia_fed.nominal_output",
    "macro.philadelphia_fed.real_output",
)
_DEFAULT_SERIES = "macro.gdp.real_qoq_saar_pct"
_FMP_PRIORITIES = ("High", "Medium", "Low", "None")
_GDP_RELEASE_STAGES = ("advance", "initial", "second", "third")
_SYMBOL = re.compile(r"^[A-Za-z0-9.^=_-]{1,32}$")
_CIK = re.compile(r"^[0-9]{10}$")
_PERIOD = re.compile(r"^[0-9]{4}(?:Q[1-4]|-[0-9]{2})$")
_COMPANY_METRICS = (
    "revenue",
    "net_income",
    "total_assets",
    "total_liabilities",
    "operating_cash_flow",
    "shares_outstanding",
    "weighted_average_shares_basic",
    "weighted_average_shares_diluted",
    "earnings_per_share_basic",
    "earnings_per_share_diluted",
)
_COMPANY_METRIC_IDS = {
    metric: stable_id("company_metric", metric) for metric in _COMPANY_METRICS
}
_COMPANY_METRIC_CODES_BY_ID = {
    metric_id: metric for metric, metric_id in _COMPANY_METRIC_IDS.items()
}
_OPTIONS_SURFACE_TARGET_DTE_TEXT = frozenset(
    str(value) for value in _OPTIONS_SURFACE_TARGET_DTES
)
_MAX_LIMIT = 100
_MAX_PAGE = 1000
_CURRENT_NEWS_TOOL_VERSION = "2.2.0"
_CURRENT_DATA_STATUS_TOOL_VERSION = "1.0.0"
_CURRENT_NEWS_QUERY_FIELDS = frozenset(
    {"query", "symbol", "source_id", "start_date", "end_date", "cursor", "limit"}
)
_CURRENT_NEWS_SOURCE_LABELS = {
    "fmp_stock_latest": "FMP stock news",
    "fmp_press_releases": "FMP press releases",
    "fmp_general": "FMP general news",
    "fed_press": "Federal Reserve",
    "ecb_press": "European Central Bank",
    "bea_news": "Bureau of Economic Analysis",
    "eia_press": "Energy Information Administration",
    "alpaca_benzinga": "Alpaca / Benzinga",
}


class CanonicalInspectorReadService:
    """Fixed query templates over server-owned canonical store paths."""

    def __init__(self, store_map: StoreMap, registry: Registry) -> None:
        self._stores = store_map
        self._registry = registry

    def inspect(self, query: Mapping[str, str]) -> dict[str, Any]:
        view = query.get("view", "market-prices")
        if view not in _VIEWS:
            raise ValidationError("Inspector view is invalid")
        allowed = {
            "market-prices": {"view", "symbol", "start_date", "end_date", "direction", "page", "limit"},
            "market-instruments": {"view", "search", "direction", "page", "limit"},
            "spy-options": {
                "view", "option_type", "state", "expiration", "direction",
                "page", "limit",
            },
            "options-surfaces": {
                "view", "underlying", "target_dte", "expiration", "option_type",
                "state", "direction", "page", "limit",
            },
            "company-fundamentals": {
                "view", "cik", "metric", "period_end", "direction", "page", "limit",
            },
            "macro-current": {"view", "series", "period", "direction", "page", "limit"},
            "macro-vintages": {"view", "series", "period", "direction", "page", "limit"},
            "macro-surprises": {
                "view", "kind", "stage", "start_date", "end_date",
                "direction", "page", "limit",
            },
            "treasury-curve": {
                "view", "tenor", "start_date", "end_date",
                "direction", "page", "limit",
            },
            "overnight-rates": {
                "view", "rate", "start_date", "end_date",
                "direction", "page", "limit",
            },
            "policy-rates": {
                "view", "series", "start_date", "end_date",
                "direction", "page", "limit",
            },
            "industrial-production": {
                "view", "series", "start_date", "end_date",
                "direction", "page", "limit",
            },
            "repo-facilities": {
                "view", "facility", "start_date", "end_date",
                "direction", "page", "limit",
            },
            "soma-summary": {
                "view", "component", "start_date", "end_date",
                "direction", "page", "limit",
            },
            "price-wage-productivity": {
                "view", "series", "start_date", "end_date",
                "direction", "page", "limit",
            },
            "personal-income-outlays": {
                "view", "series", "start_date", "end_date",
                "direction", "page", "limit",
            },
            "h41-liquidity": {
                "view", "series", "start_date", "end_date",
                "direction", "page", "limit",
            },
            "treasury-cash": {
                "view", "series", "start_date", "end_date",
                "direction", "page", "limit",
            },
            "treasury-debt": {
                "view", "series", "start_date", "end_date",
                "direction", "page", "limit",
            },
            "fiscal-balance": {
                "view", "series", "start_date", "end_date",
                "direction", "page", "limit",
            },
            "financial-conditions": {
                "view", "series", "start_date", "end_date",
                "direction", "page", "limit",
            },
            "national-activity": {
                "view", "series", "start_date", "end_date",
                "direction", "page", "limit",
            },
            "bis-credit": {
                "view", "series", "start_date", "end_date",
                "direction", "page", "limit",
            },
            "credit-market-distress": {
                "view", "series", "start_date", "end_date",
                "direction", "page", "limit",
            },
            "natural-gas-storage": {
                "view", "series", "start_date", "end_date",
                "direction", "page", "limit",
            },
            "petroleum-fundamentals": {
                "view", "series", "start_date", "end_date",
                "direction", "page", "limit",
            },
            "electricity-retail": {
                "view", "series", "start_period", "end_period",
                "direction", "page", "limit",
            },
            "crude-oil-stocks": {
                "view", "start_date", "end_date",
                "direction", "page", "limit",
            },
            "recession-chronology": {
                "view", "series", "start_date", "end_date",
                "direction", "page", "limit",
            },
            "fmp-economic-calendar": {
                "view", "mode", "country", "priority", "event_name", "keyword",
                "start_date", "end_date", "direction", "page", "limit",
            },
        }[view]
        unknown = sorted(set(query) - allowed)
        if unknown:
            raise ValidationError(
                "Inspector query contains an unsupported field",
                issues=(Issue(f"/{unknown[0]}", "additional_properties", "Field is not supported"),),
            )
        direction = query.get("direction", "desc")
        if direction not in {"asc", "desc"}:
            raise ValidationError("Inspector direction is invalid")
        page = _bounded_int(query.get("page", "1"), "/page", 1, _MAX_PAGE)
        limit = _bounded_int(query.get("limit", "25"), "/limit", 1, _MAX_LIMIT)
        if view == "market-prices":
            result = self._market_prices(query, direction, page, limit)
        elif view == "market-instruments":
            result = self._market_instruments(query, direction, page, limit)
        elif view == "spy-options":
            result = self._spy_options(query, direction, page, limit)
        elif view == "options-surfaces":
            result = self._options_surfaces(query, direction, page, limit)
        elif view == "company-fundamentals":
            result = self._company_fundamentals(query, direction, page, limit)
        elif view == "macro-current":
            result = self._macro_rows(query, direction, page, limit, current=True)
        elif view == "macro-vintages":
            result = self._macro_rows(query, direction, page, limit, current=False)
        elif view == "macro-surprises":
            result = self._macro_surprises(query, direction, page, limit)
        elif view == "treasury-curve":
            result = self._treasury_curve(query, direction, page, limit)
        elif view == "overnight-rates":
            result = self._overnight_rates(query, direction, page, limit)
        elif view == "repo-facilities":
            result = self._repo_facilities(query, direction, page, limit)
        elif view == "soma-summary":
            result = self._soma_summary(query, direction, page, limit)
        elif view == "crude-oil-stocks":
            result = self._eia_petroleum_weekly(
                query, direction, page, limit
            )
        elif view == "electricity-retail":
            result = self._eia_electricity_retail(
                query, direction, page, limit
            )
        elif view in _OFFICIAL_VIEW_MANIFESTS:
            result = self._official_series(
                view, query, direction, page, limit
            )
        else:
            result = self._fmp_economic_calendar(query, direction, page, limit)
        result.update({"view": view, "page": page, "limit": limit, "direction": direction})
        return result

    def _market_prices(
        self, query: Mapping[str, str], direction: str, page: int, limit: int
    ) -> dict[str, Any]:
        symbol = query.get("symbol", "AAPL").strip().upper()
        if _SYMBOL.fullmatch(symbol) is None:
            raise ValidationError("Market symbol is invalid")
        start = _optional_date(query.get("start_date"), "/start_date")
        end = _optional_date(query.get("end_date"), "/end_date")
        if start and end and start > end:
            raise ValidationError("Market date range is invalid")
        where = ["instrument.provider='fmp'", "instrument.provider_symbol=?"]
        parameters: list[object] = [symbol]
        if start:
            where.append("price.trade_date>=?")
            parameters.append(start)
        if end:
            where.append("price.trade_date<=?")
            parameters.append(end)
        columns = (
            "symbol", "name", "type", "trade_date", "open", "high", "low", "close",
            "volume", "available_at", "captured_at",
        )
        sql = f"""
            SELECT instrument.provider_symbol AS symbol,
                   instrument.display_name AS name,
                   instrument.asset_type AS type,
                   price.trade_date,
                   version.open_value AS open,
                   version.high_value AS high,
                   version.low_value AS low,
                   version.close_value AS close,
                   version.volume,
                   version.available_at,
                   version.captured_at
            FROM stage10_daily_prices AS price
            JOIN stage10_instruments AS instrument
              ON instrument.instrument_id=price.instrument_id
            JOIN stage10_daily_price_versions AS version
              ON version.version_id=price.current_version_id
            WHERE {' AND '.join(where)}
            ORDER BY price.trade_date {direction.upper()}
            LIMIT ? OFFSET ?
        """
        count_sql = f"""
            SELECT COUNT(*)
            FROM stage10_daily_prices AS price
            JOIN stage10_instruments AS instrument
              ON instrument.instrument_id=price.instrument_id
            WHERE {' AND '.join(where)}
        """
        with _immutable_store_connection(
            self._stores.market, expected_role="market"
        ) as connection:
            total = int(connection.execute(count_sql, tuple(parameters)).fetchone()[0])
            rows = _rows(
                connection.execute(
                    sql, (*parameters, limit, (page - 1) * limit)
                ).fetchall()
            )
        return _result(columns, rows, total, page, limit, {"symbol": symbol, "start_date": start, "end_date": end})

    def _market_instruments(
        self, query: Mapping[str, str], direction: str, page: int, limit: int
    ) -> dict[str, Any]:
        search = query.get("search", "").strip()
        if len(search) > 64 or any(ord(char) < 32 for char in search):
            raise ValidationError("Market symbol search is invalid")
        where = ["provider='fmp'"]
        parameters: list[object] = []
        if search:
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where.append("(provider_symbol LIKE ? ESCAPE '\\' OR display_name LIKE ? ESCAPE '\\')")
            parameters.extend((f"%{escaped}%", f"%{escaped}%"))
        columns = ("symbol", "name", "type", "exchange", "first_trade_date")
        sql = f"""
            SELECT provider_symbol AS symbol, display_name AS name, asset_type AS type,
                   exchange_code AS exchange, first_trade_date
            FROM stage10_instruments
            WHERE {' AND '.join(where)}
            ORDER BY provider_symbol {direction.upper()}
            LIMIT ? OFFSET ?
        """
        count_sql = f"SELECT COUNT(*) FROM stage10_instruments WHERE {' AND '.join(where)}"
        with _immutable_store_connection(
            self._stores.market, expected_role="market"
        ) as connection:
            total = int(connection.execute(count_sql, tuple(parameters)).fetchone()[0])
            rows = _rows(connection.execute(sql, (*parameters, limit, (page - 1) * limit)).fetchall())
        return _result(columns, rows, total, page, limit, {"search": search})

    def _spy_options(
        self, query: Mapping[str, str], direction: str, page: int, limit: int
    ) -> dict[str, Any]:
        """Inspect only the latest fixed Alpaca indicative SPY capture."""

        option_type = query.get("option_type", "").lower()
        if option_type and option_type not in {"call", "put"}:
            raise ValidationError("SPY option type is invalid")
        state = query.get("state", "").lower()
        if state and state not in {"present", "missing", "excluded"}:
            raise ValidationError("SPY option surface state is invalid")
        expiration = _optional_date(query.get("expiration"), "/expiration")

        where: list[str] = []
        parameters: list[object] = []
        if option_type:
            where.append("contract.option_type=?")
            parameters.append(option_type)
        if state:
            where.append("surface.surface_state=?")
            parameters.append(state)
        if expiration:
            where.append("contract.expiration_date=?")
            parameters.append(expiration)
        predicate = " AND ".join(where) if where else "1=1"
        latest = """
            WITH latest_capture AS (
                SELECT capture.capture_id, capture.completed_at,
                       capture.resolved_feed, capture.completeness
                FROM option_surface_captures AS capture
                JOIN stage10_instruments AS underlying
                  ON underlying.instrument_id=capture.underlying_instrument_id
                WHERE underlying.provider='fmp'
                  AND underlying.provider_symbol='SPY'
                  AND capture.environment='paper'
                  AND capture.requested_feed='indicative'
                  AND capture.resolved_feed='alpaca_indicative'
                ORDER BY capture.completed_at DESC, capture.capture_id DESC
                LIMIT 1
            )
        """
        base = f"""
            FROM latest_capture AS capture
            JOIN option_surface_snapshots AS surface
              ON surface.capture_id=capture.capture_id
            JOIN option_contracts AS contract
              ON contract.contract_id=surface.contract_id
            LEFT JOIN option_capture_underlying_quotes AS underlying_quote
              ON underlying_quote.capture_id=capture.capture_id
            WHERE {predicate}
        """
        columns = (
            "capture_id", "captured_at", "feed", "completeness",
            "expiration_date", "contract_symbol", "option_type",
            "strike_price", "contract_status", "deliverable_kind",
            "surface_state", "bid_price", "ask_price", "last_price",
            "implied_volatility", "delta", "gamma", "theta", "vega", "rho",
            "open_interest", "close_price", "quote_at", "trade_at",
            "underlying_last_price", "underlying_quote_at", "missing_reason",
            "exclusion_reason",
        )
        sql = (
            latest
            + """
            SELECT capture.capture_id,
                   capture.completed_at AS captured_at,
                   capture.resolved_feed AS feed,
                   capture.completeness,
                   contract.expiration_date,
                   contract.contract_symbol,
                   contract.option_type,
                   contract.strike_price,
                   contract.contract_status,
                   contract.deliverable_kind,
                   surface.surface_state,
                   surface.bid_price,
                   surface.ask_price,
                   surface.last_price,
                   surface.implied_volatility,
                   surface.delta,
                   surface.gamma,
                   surface.theta,
                   surface.vega,
                   surface.rho,
                   (
                       SELECT interest.open_interest
                       FROM option_open_interest AS interest
                       WHERE interest.capture_id=surface.capture_id
                         AND interest.contract_id=surface.contract_id
                       ORDER BY interest.as_of_date DESC, interest.source_row DESC
                       LIMIT 1
                   ) AS open_interest,
                   (
                       SELECT close_price.close_price
                       FROM option_close_prices AS close_price
                       WHERE close_price.capture_id=surface.capture_id
                         AND close_price.contract_id=surface.contract_id
                       ORDER BY close_price.trade_date DESC,
                                close_price.source_row DESC
                       LIMIT 1
                   ) AS close_price,
                   surface.quote_at,
                   surface.trade_at,
                   underlying_quote.last_price AS underlying_last_price,
                   underlying_quote.quote_at AS underlying_quote_at,
                   surface.missing_reason,
                   surface.exclusion_reason
            """
            + base
            + f"""
            ORDER BY contract.expiration_date {direction.upper()},
                     contract.option_type ASC,
                     CAST(contract.strike_price AS REAL) {direction.upper()},
                     contract.contract_symbol ASC
            LIMIT ? OFFSET ?
            """
        )
        with _immutable_store_connection(
            self._stores.market, expected_role="market"
        ) as connection:
            total = int(
                connection.execute(
                    latest + "SELECT COUNT(*) " + base, tuple(parameters)
                ).fetchone()[0]
            )
            rows = _rows(
                connection.execute(
                    sql, (*parameters, limit, (page - 1) * limit)
                ).fetchall()
            )
        return _result(
            columns,
            rows,
            total,
            page,
            limit,
            {
                "option_type": option_type,
                "state": state,
                "expiration": expiration,
            },
        )

    def _options_surfaces(
        self, query: Mapping[str, str], direction: str, page: int, limit: int
    ) -> dict[str, Any]:
        """Inspect latest paper/indicative Alpaca captures for each expiry cohort."""

        underlying = query.get("underlying", "")
        if underlying and underlying not in _OPTIONS_SURFACE_UNDERLYINGS:
            raise ValidationError("Options underlying is invalid")
        target_dte = query.get("target_dte", "")
        if target_dte and target_dte not in _OPTIONS_SURFACE_TARGET_DTE_TEXT:
            raise ValidationError("Options target DTE is invalid")
        option_type = query.get("option_type", "")
        if option_type and option_type not in {"call", "put"}:
            raise ValidationError("Options option type is invalid")
        state = query.get("state", "")
        if state and state not in {"present", "missing", "excluded"}:
            raise ValidationError("Options surface state is invalid")
        expiration = _optional_date(query.get("expiration"), "/expiration")

        where = ["capture.capture_rank=1"]
        parameters: list[object] = []
        if underlying:
            where.append("capture.underlying_symbol=?")
            parameters.append(underlying)
        if target_dte:
            where.append(
                "EXISTS (SELECT 1 FROM json_each(capture.target_dtes) AS target "
                "WHERE CAST(target.value AS TEXT)=?)"
            )
            parameters.append(target_dte)
        if option_type:
            where.append("contract.option_type=?")
            parameters.append(option_type)
        if state:
            where.append("surface.surface_state=?")
            parameters.append(state)
        if expiration:
            where.append("contract.expiration_date=?")
            parameters.append(expiration)
        predicate = " AND ".join(where)
        latest = """
            WITH eligible_captures AS (
                SELECT capture.capture_id,
                       capture.requested_feed,
                       capture.resolved_feed,
                       capture.environment,
                       capture.completed_at,
                       capture.completeness,
                       underlying.provider_symbol AS underlying_symbol,
                       CASE WHEN json_valid(capture.request_scope_json)=1 THEN
                           CASE WHEN json_type(
                               capture.request_scope_json, '$.selected_expiration'
                           )='text' THEN json_extract(
                               capture.request_scope_json, '$.selected_expiration'
                           ) END
                       END AS selected_expiration,
                       CASE WHEN json_valid(capture.request_scope_json)=1 THEN
                           CASE
                               WHEN json_type(
                                   capture.request_scope_json, '$.target_dtes'
                               )='array' THEN json_extract(
                                   capture.request_scope_json, '$.target_dtes'
                               )
                               WHEN json_type(
                                   capture.request_scope_json, '$.target_dte'
                               )='integer' THEN json_array(json_extract(
                                   capture.request_scope_json, '$.target_dte'
                               ))
                               ELSE json_array()
                           END
                       ELSE json_array() END AS target_dtes,
                       CASE WHEN json_valid(capture.request_scope_json)=1
                              AND json_type(
                                  capture.request_scope_json, '$.spot_price'
                              ) IN ('integer', 'real', 'text')
                            THEN json_extract(
                                capture.request_scope_json, '$.spot_price'
                            )
                       END AS underlying_spot_price
                FROM option_surface_captures AS capture
                JOIN stage10_instruments AS underlying
                  ON underlying.instrument_id=capture.underlying_instrument_id
                WHERE underlying.provider='fmp'
                  AND underlying.provider_symbol IN (
                      'SPY', 'QQQ', 'IWM', 'DIA', 'XLB', 'XLC', 'XLE', 'XLF',
                      'XLI', 'XLK', 'XLP', 'XLRE', 'XLU', 'XLV', 'XLY'
                  )
                  AND capture.environment='paper'
                  AND capture.requested_feed='indicative'
                  AND capture.resolved_feed='alpaca_indicative'
            ),
            ranked_captures AS (
                SELECT eligible_captures.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY underlying_symbol, selected_expiration
                           ORDER BY completed_at DESC, capture_id DESC
                       ) AS capture_rank
                FROM eligible_captures
                WHERE selected_expiration IS NOT NULL
            )
        """
        base = f"""
            FROM ranked_captures AS capture
            JOIN option_surface_snapshots AS surface
              ON surface.capture_id=capture.capture_id
            JOIN option_contracts AS contract
              ON contract.contract_id=surface.contract_id
            LEFT JOIN option_capture_underlying_quotes AS underlying_quote
              ON underlying_quote.capture_id=capture.capture_id
            LEFT JOIN option_capture_expiry_inputs AS expiry_input
              ON expiry_input.capture_id=capture.capture_id
             AND expiry_input.expiration_date=capture.selected_expiration
            LEFT JOIN option_capture_rate_curves AS rate_curve
              ON rate_curve.capture_id=capture.capture_id
            LEFT JOIN option_capture_dividend_sets AS dividend_set
              ON dividend_set.capture_id=capture.capture_id
            WHERE {predicate}
        """
        columns = (
            "underlying_symbol", "target_dtes", "capture_id", "captured_at",
            "requested_feed", "resolved_feed", "environment", "completeness",
            "selected_expiration", "input_state", "input_missing_reason",
            "input_spot_price", "risk_free_rate", "dividend_yield", "forward_price",
            "rate_curve_state", "rate_curve_missing_reason", "rate_curve_date",
            "rate_curve_source_name", "dividend_set_state",
            "dividend_set_missing_reason", "dividend_source_name",
            "contract_id", "contract_symbol", "expiration_date", "option_type",
            "strike_price", "contract_status", "deliverable_kind", "surface_state",
            "bid_price", "ask_price", "last_price", "implied_volatility", "delta",
            "gamma", "theta", "vega", "rho", "open_interest",
            "open_interest_as_of_date", "open_interest_state",
            "open_interest_missing_reason", "close_price", "close_price_trade_date",
            "close_price_state", "close_price_missing_reason",
            "quote_at", "trade_at", "underlying_spot_price",
            "underlying_quote_state", "underlying_quote_missing_reason",
            "underlying_bid_price", "underlying_ask_price", "underlying_last_price",
            "underlying_trade_price", "underlying_quote_at", "missing_reason",
            "exclusion_reason",
        )
        sql = (
            latest
            + """
            SELECT capture.underlying_symbol,
                   capture.target_dtes,
                   capture.capture_id,
                   capture.completed_at AS captured_at,
                   capture.requested_feed,
                   capture.resolved_feed,
                   capture.environment,
                   capture.completeness,
                   capture.selected_expiration,
                   expiry_input.input_state,
                   expiry_input.missing_reason AS input_missing_reason,
                   expiry_input.spot_price AS input_spot_price,
                   expiry_input.risk_free_rate,
                   expiry_input.dividend_yield,
                   expiry_input.forward_price,
                   rate_curve.input_state AS rate_curve_state,
                   rate_curve.missing_reason AS rate_curve_missing_reason,
                   rate_curve.curve_date AS rate_curve_date,
                   rate_curve.source_name AS rate_curve_source_name,
                   dividend_set.input_state AS dividend_set_state,
                   dividend_set.missing_reason AS dividend_set_missing_reason,
                   dividend_set.source_name AS dividend_source_name,
                   contract.contract_id,
                   contract.contract_symbol,
                   contract.expiration_date,
                   contract.option_type,
                   contract.strike_price,
                   contract.contract_status,
                   contract.deliverable_kind,
                   surface.surface_state,
                   surface.bid_price,
                   surface.ask_price,
                   surface.last_price,
                   surface.implied_volatility,
                   surface.delta,
                   surface.gamma,
                   surface.theta,
                   surface.vega,
                   surface.rho,
                   (
                       SELECT interest.open_interest
                       FROM option_open_interest AS interest
                       WHERE interest.capture_id=surface.capture_id
                         AND interest.contract_id=surface.contract_id
                       ORDER BY interest.as_of_date DESC, interest.source_row DESC
                       LIMIT 1
                   ) AS open_interest,
                   (
                       SELECT interest.as_of_date
                       FROM option_open_interest AS interest
                       WHERE interest.capture_id=surface.capture_id
                         AND interest.contract_id=surface.contract_id
                       ORDER BY interest.as_of_date DESC, interest.source_row DESC
                       LIMIT 1
                   ) AS open_interest_as_of_date,
                   (
                       SELECT interest.observation_state
                       FROM option_open_interest AS interest
                       WHERE interest.capture_id=surface.capture_id
                         AND interest.contract_id=surface.contract_id
                       ORDER BY interest.as_of_date DESC, interest.source_row DESC
                       LIMIT 1
                   ) AS open_interest_state,
                   (
                       SELECT interest.missing_reason
                       FROM option_open_interest AS interest
                       WHERE interest.capture_id=surface.capture_id
                         AND interest.contract_id=surface.contract_id
                       ORDER BY interest.as_of_date DESC, interest.source_row DESC
                       LIMIT 1
                   ) AS open_interest_missing_reason,
                   (
                       SELECT close_price.close_price
                       FROM option_close_prices AS close_price
                       WHERE close_price.capture_id=surface.capture_id
                         AND close_price.contract_id=surface.contract_id
                       ORDER BY close_price.trade_date DESC, close_price.source_row DESC
                       LIMIT 1
                   ) AS close_price,
                   (
                       SELECT close_price.trade_date
                       FROM option_close_prices AS close_price
                       WHERE close_price.capture_id=surface.capture_id
                         AND close_price.contract_id=surface.contract_id
                       ORDER BY close_price.trade_date DESC, close_price.source_row DESC
                       LIMIT 1
                   ) AS close_price_trade_date,
                   (
                       SELECT close_price.observation_state
                       FROM option_close_prices AS close_price
                       WHERE close_price.capture_id=surface.capture_id
                         AND close_price.contract_id=surface.contract_id
                       ORDER BY close_price.trade_date DESC, close_price.source_row DESC
                       LIMIT 1
                   ) AS close_price_state,
                   (
                       SELECT close_price.missing_reason
                       FROM option_close_prices AS close_price
                       WHERE close_price.capture_id=surface.capture_id
                         AND close_price.contract_id=surface.contract_id
                       ORDER BY close_price.trade_date DESC, close_price.source_row DESC
                       LIMIT 1
                   ) AS close_price_missing_reason,
                   surface.quote_at,
                   surface.trade_at,
                   capture.underlying_spot_price,
                   underlying_quote.input_state AS underlying_quote_state,
                   underlying_quote.missing_reason AS underlying_quote_missing_reason,
                   underlying_quote.bid_price AS underlying_bid_price,
                   underlying_quote.ask_price AS underlying_ask_price,
                   underlying_quote.last_price AS underlying_last_price,
                   underlying_quote.trade_price AS underlying_trade_price,
                   underlying_quote.quote_at AS underlying_quote_at,
                   surface.missing_reason,
                   surface.exclusion_reason
            """
            + base
            + f"""
            ORDER BY capture.underlying_symbol ASC,
                     capture.selected_expiration {direction.upper()},
                     contract.option_type ASC,
                     CAST(contract.strike_price AS REAL) {direction.upper()},
                     contract.contract_symbol ASC
            LIMIT ? OFFSET ?
            """
        )
        with _immutable_store_connection(
            self._stores.market, expected_role="market"
        ) as connection:
            total = int(
                connection.execute(
                    latest + "SELECT COUNT(*) " + base, tuple(parameters)
                ).fetchone()[0]
            )
            rows = _rows(
                connection.execute(
                    sql, (*parameters, limit, (page - 1) * limit)
                ).fetchall()
            )
        for row in rows:
            row["target_dtes"] = _option_target_dtes(row["target_dtes"])
        return _result(
            columns,
            rows,
            total,
            page,
            limit,
            {
                "underlying": underlying,
                "target_dte": target_dte,
                "expiration": expiration,
                "option_type": option_type,
                "state": state,
            },
        )

    def _company_fundamentals(
        self, query: Mapping[str, str], direction: str, page: int, limit: int
    ) -> dict[str, Any]:
        """Inspect latest normalized facts for the reviewed core metrics."""

        cik = query.get("cik", "").strip()
        if cik and _CIK.fullmatch(cik) is None:
            raise ValidationError("Company CIK is invalid")
        metric = query.get("metric", "").strip()
        if metric and metric not in _COMPANY_METRICS:
            raise ValidationError("Company fundamental metric is invalid")
        period_end = _optional_date(query.get("period_end"), "/period_end")

        metric_placeholders = ", ".join("?" for _ in _COMPANY_METRIC_IDS)
        where = [
            "fundamental.current_rank=1",
            f"fundamental.metric_id IN ({metric_placeholders})",
        ]
        parameters: list[object] = list(_COMPANY_METRIC_IDS.values())
        if cik:
            where.append("issuer.cik=?")
            parameters.append(cik)
        if metric:
            where.append("fundamental.metric_id=?")
            parameters.append(_COMPANY_METRIC_IDS[metric])
        if period_end:
            where.append("fundamental.reference_period_end=?")
            parameters.append(period_end)

        common_table = """
            WITH latest_issuer_version AS (
                SELECT issuer_version_id, issuer_id, legal_name, name_state,
                       version_sequence,
                       ROW_NUMBER() OVER (
                           PARTITION BY issuer_id
                           ORDER BY version_sequence DESC, issuer_version_id DESC
                       ) AS current_rank
                FROM company_issuer_versions
            ),
            current_fundamentals AS (
                SELECT fundamental.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY issuer_id, metric_id,
                                        reference_period_end
                           ORDER BY version_sequence DESC,
                                    fundamental_version_id DESC
                       ) AS current_rank
                FROM company_fundamental_observation_versions AS fundamental
            )
        """
        base = f"""
            FROM current_fundamentals AS fundamental
            JOIN company_issuers AS issuer
              ON issuer.issuer_id=fundamental.issuer_id
            LEFT JOIN latest_issuer_version AS issuer_version
              ON issuer_version.issuer_id=issuer.issuer_id
             AND issuer_version.current_rank=1
            JOIN company_metric_definitions AS metric_definition
              ON metric_definition.metric_id=fundamental.metric_id
            JOIN company_metric_mappings AS mapping
              ON mapping.mapping_id=fundamental.mapping_id
            JOIN company_sec_filings AS filing
              ON filing.accession_number=fundamental.accession_number
            WHERE {' AND '.join(where)}
        """
        columns = (
            "cik",
            "legal_name",
            "metric",
            "metric_name",
            "period_start",
            "period_end",
            "fiscal_year",
            "fiscal_period",
            "value",
            "value_state",
            "missing_reason",
            "unit",
            "share_semantics",
            "filing_form",
            "filing_date",
            "accession_number",
            "available_at",
            "available_precision",
            "version_sequence",
            "taxonomy",
            "source_concept",
            "mapping_version",
        )
        sql = f"""
            {common_table}
            SELECT issuer.cik,
                   CASE issuer_version.name_state
                        WHEN 'present' THEN issuer_version.legal_name
                        ELSE NULL
                   END AS legal_name,
                   fundamental.metric_id AS metric,
                   metric_definition.display_name AS metric_name,
                   fundamental.reference_period_start AS period_start,
                   fundamental.reference_period_end AS period_end,
                   fundamental.fiscal_year,
                   fundamental.fiscal_period,
                   fundamental.value_text AS value,
                   fundamental.value_state,
                   fundamental.missing_reason,
                   mapping.unit,
                   fundamental.share_semantics,
                   filing.form_type AS filing_form,
                   filing.filing_date,
                   fundamental.accession_number,
                   fundamental.available_at,
                   fundamental.available_precision,
                   fundamental.version_sequence,
                   mapping.taxonomy,
                   mapping.concept AS source_concept,
                   mapping.mapping_version
            {base}
            ORDER BY fundamental.reference_period_end {direction.upper()},
                     issuer.cik ASC,
                     fundamental.metric_id ASC,
                     fundamental.version_sequence DESC,
                     fundamental.fundamental_version_id ASC
            LIMIT ? OFFSET ?
        """
        with _immutable_store_connection(
            self._stores.company, expected_role="company"
        ) as connection:
            total = int(
                connection.execute(
                    f"{common_table} SELECT COUNT(*) {base}", tuple(parameters)
                ).fetchone()[0]
            )
            rows = _rows(
                connection.execute(
                    sql, (*parameters, limit, (page - 1) * limit)
                ).fetchall()
            )
        for row in rows:
            row["metric"] = _COMPANY_METRIC_CODES_BY_ID[str(row["metric"])]
        return _result(
            columns,
            rows,
            total,
            page,
            limit,
            {"cik": cik, "metric": metric, "period_end": period_end},
        )

    def _macro_rows(
        self,
        query: Mapping[str, str],
        direction: str,
        page: int,
        limit: int,
        *,
        current: bool,
    ) -> dict[str, Any]:
        series = query.get("series", _DEFAULT_SERIES)
        if series not in _MACRO_SERIES:
            raise ValidationError("Macro series is invalid")
        period = query.get("period", "").strip()
        if period and _PERIOD.fullmatch(period) is None:
            raise ValidationError("Macro period is invalid")
        where = ["version.series_id=?"]
        parameters: list[object] = [series]
        if period:
            where.append("version.period=?")
            parameters.append(period)
        current_join = (
            "JOIN macro_live_vintage_observations AS observation "
            "ON observation.current_version_id=version.version_id"
            if current
            else ""
        )
        columns = (
            "series", "title", "period", "value", "unit", "source_vintage",
            "vintage_at", "release_stage", "first_release", "correction",
            "available_at", "captured_at",
        )
        base = f"""
            FROM macro_live_vintage_observation_versions AS version
            {current_join}
            JOIN macro_live_vintage_releases AS release
              ON release.release_id=version.release_id
            JOIN macro_live_vintage_series AS series
              ON series.series_id=version.series_id
            WHERE {' AND '.join(where)}
        """
        sql = f"""
            SELECT version.series_id AS series, series.title, version.period,
                   version.value_text AS value, version.unit,
                   release.source_vintage_identity AS source_vintage,
                   release.vintage_at, release.release_stage,
                   release.is_first_release AS first_release,
                   version.correction_sequence AS correction,
                   version.available_at, version.captured_at
            {base}
            ORDER BY version.period {direction.upper()},
                     release.source_release_order {direction.upper()},
                     version.correction_sequence {direction.upper()}
            LIMIT ? OFFSET ?
        """
        with read_connection(self._stores, StoreRole.MACRO) as connection:
            total = int(connection.execute(f"SELECT COUNT(*) {base}", tuple(parameters)).fetchone()[0])
            rows = _rows(connection.execute(sql, (*parameters, limit, (page - 1) * limit)).fetchall())
        return _result(columns, rows, total, page, limit, {"series": series, "period": period})

    def _macro_surprises(
        self, query: Mapping[str, str], direction: str, page: int, limit: int
    ) -> dict[str, Any]:
        kind = query.get("kind", GDP_ADVANCE_KIND)
        if kind not in ALL_SURPRISE_KINDS:
            raise ValidationError("Macro surprise kind is invalid")
        stage = query.get("stage", "")
        if stage and (kind != GDP_ADVANCE_KIND or stage not in _GDP_RELEASE_STAGES):
            raise ValidationError("Macro surprise release stage is invalid")
        start = _optional_date(query.get("start_date"), "/start_date")
        end = _optional_date(query.get("end_date"), "/end_date")
        if start and end and start > end:
            raise ValidationError("Macro surprise date range is invalid")
        repository = MacroReleaseSurpriseRepository(
            macro_store=self._stores.macro,
            registry=self._registry,
        )
        selected = list(repository.query(start_date=start, end_date=end, kinds=(kind,)))
        if stage:
            selected = [item for item in selected if item.release_stage == stage]
        if direction == "desc":
            selected.reverse()
        total = len(selected)
        page_rows = selected[(page - 1) * limit : page * limit]
        rows = [
            {
                "event_at": item.event_at,
                "reference_period": item.reference_period,
                "kind": item.kind,
                "consensus": _decimal(item.consensus),
                "fmp_actual": _decimal(item.fmp_actual),
                "official_actual": _decimal(item.official_actual),
                "surprise": _decimal(item.surprise),
                "unit": item.unit,
                "actual_source": item.actual_source,
                "mapping": item.consensus_mapping_basis,
                "status": item.status,
                "official_version_id": item.official_version_id,
                "official_prior_version_id": item.official_prior_version_id,
                "release_stage": item.release_stage,
                "is_fallback": item.is_fallback,
                "coalesced_event_version_ids": item.coalesced_event_version_ids,
            }
            for item in page_rows
        ]
        columns = (
            "event_at", "reference_period", "kind", "consensus", "official_actual",
            "fmp_actual", "surprise", "unit", "actual_source", "mapping", "status",
            "official_version_id", "official_prior_version_id",
            "release_stage", "is_fallback",
            "coalesced_event_version_ids",
        )
        return _result(
            columns,
            rows,
            total,
            page,
            limit,
            {
                "kind": kind,
                "stage": stage,
                "start_date": start,
                "end_date": end,
            },
        )

    def _treasury_curve(
        self, query: Mapping[str, str], direction: str, page: int, limit: int
    ) -> dict[str, Any]:
        tenor = query.get("tenor", "")
        if tenor and tenor not in _TREASURY_TENORS:
            raise ValidationError("Treasury tenor is invalid")
        start = _optional_date(query.get("start_date"), "/start_date")
        end = _optional_date(query.get("end_date"), "/end_date")
        if start and end and start > end:
            raise ValidationError("Treasury curve date range is invalid")

        where = ["provider=?", "curve_variant=?"]
        parameters: list[object] = [PROVIDER, CURVE_VARIANT]
        if tenor:
            where.append("tenor=?")
            parameters.append(tenor)
        if start:
            where.append("curve_date>=?")
            parameters.append(start)
        if end:
            where.append("curve_date<=?")
            parameters.append(end)
        predicate = " AND ".join(where)
        latest = f"""
            WITH latest AS (
                SELECT provider, curve_date, curve_variant, tenor,
                       MAX(correction_sequence) AS correction_sequence
                FROM treasury_yield_curve_versions
                WHERE {predicate}
                GROUP BY provider, curve_date, curve_variant, tenor
            )
        """
        base = """
            FROM treasury_yield_curve_versions AS version
            JOIN latest
              ON latest.provider=version.provider
             AND latest.curve_date=version.curve_date
             AND latest.curve_variant=version.curve_variant
             AND latest.tenor=version.tenor
             AND latest.correction_sequence=version.correction_sequence
        """
        tenor_order = "CASE version.tenor " + " ".join(
            f"WHEN '{item}' THEN {ordinal}"
            for ordinal, item in enumerate(_TREASURY_TENORS, start=1)
        ) + " ELSE 99 END"
        columns = (
            "curve_date", "tenor", "yield_percent", "missing_reason",
            "correction", "available_at", "captured_at",
        )
        sql = (
            latest
            + """
            SELECT version.curve_date, version.tenor,
                   version.yield_value AS yield_percent,
                   version.missing_reason,
                   version.correction_sequence AS correction,
                   version.available_at, version.captured_at
            """
            + base
            + f"""
            ORDER BY version.curve_date {direction.upper()}, {tenor_order} ASC
            LIMIT ? OFFSET ?
            """
        )
        with _immutable_store_connection(
            self._stores.macro, expected_role="macro"
        ) as connection:
            total = int(
                connection.execute(
                    latest + "SELECT COUNT(*) " + base,
                    tuple(parameters),
                ).fetchone()[0]
            )
            rows = _rows(
                connection.execute(
                    sql,
                    (*parameters, limit, (page - 1) * limit),
                ).fetchall()
            )
        return _result(
            columns,
            rows,
            total,
            page,
            limit,
            {"tenor": tenor, "start_date": start, "end_date": end},
        )

    def _overnight_rates(
        self, query: Mapping[str, str], direction: str, page: int, limit: int
    ) -> dict[str, Any]:
        rate = query.get("rate", "").upper()
        if rate and rate not in _OVERNIGHT_RATE_CODES:
            raise ValidationError("Overnight rate is invalid")
        start = _optional_date(query.get("start_date"), "/start_date")
        end = _optional_date(query.get("end_date"), "/end_date")
        if start and end and start > end:
            raise ValidationError("Overnight-rate date range is invalid")

        placeholders = ", ".join("?" for _ in _OVERNIGHT_RATE_CODES)
        where = [
            "series.provider=?",
            f"series.provider_series_code IN ({placeholders})",
        ]
        parameters: list[object] = [NYFED_PROVIDER, *_OVERNIGHT_RATE_CODES]
        if rate:
            where.append("series.provider_series_code=?")
            parameters.append(rate)
        if start:
            where.append("version.period_start>=?")
            parameters.append(start)
        if end:
            where.append("version.period_start<=?")
            parameters.append(end)
        predicate = " AND ".join(where)
        base = f"""
            FROM macro_observations AS observation
            JOIN macro_observation_versions AS version
              ON version.version_id=observation.current_version_id
            JOIN macro_series AS series
              ON series.series_id=version.series_id
            WHERE {predicate}
        """
        rate_order = "CASE series.provider_series_code " + " ".join(
            f"WHEN '{item}' THEN {ordinal}"
            for ordinal, item in enumerate(_OVERNIGHT_RATE_CODES, start=1)
        ) + " ELSE 99 END"
        columns = (
            "effective_date",
            "rate",
            "title",
            "value",
            "unit",
            "missing_reason",
            "correction",
            "available_at",
            "captured_at",
        )
        sql = (
            """
            SELECT version.period_start AS effective_date,
                   series.provider_series_code AS rate,
                   series.title,
                   version.value_text AS value,
                   version.unit,
                   version.missing_reason,
                   version.correction_sequence AS correction,
                   version.available_at,
                   version.captured_at
            """
            + base
            + f"""
            ORDER BY version.period_start {direction.upper()}, {rate_order} ASC
            LIMIT ? OFFSET ?
            """
        )
        with _immutable_store_connection(
            self._stores.macro, expected_role="macro"
        ) as connection:
            total = int(
                connection.execute(
                    "SELECT COUNT(*) " + base, tuple(parameters)
                ).fetchone()[0]
            )
            rows = _rows(
                connection.execute(
                    sql,
                    (*parameters, limit, (page - 1) * limit),
                ).fetchall()
            )
        return _result(
            columns,
            rows,
            total,
            page,
            limit,
            {"rate": rate, "start_date": start, "end_date": end},
        )

    def _repo_facilities(
        self, query: Mapping[str, str], direction: str, page: int, limit: int
    ) -> dict[str, Any]:
        facility = query.get("facility", "").upper()
        if facility and facility not in _REPO_FACILITY_CODES:
            raise ValidationError("Repo facility is invalid")
        start = _optional_date(query.get("start_date"), "/start_date")
        end = _optional_date(query.get("end_date"), "/end_date")
        if start and end and start > end:
            raise ValidationError("Repo-facility date range is invalid")

        placeholders = ", ".join("?" for _ in _REPO_FACILITY_CODES)
        where = [
            "series.provider=?",
            f"series.provider_series_code IN ({placeholders})",
        ]
        parameters: list[object] = [
            NYFED_REPO_PROVIDER,
            *_REPO_FACILITY_CODES,
        ]
        if facility:
            where.append("series.provider_series_code=?")
            parameters.append(facility)
        if start:
            where.append("version.period_start>=?")
            parameters.append(start)
        if end:
            where.append("version.period_start<=?")
            parameters.append(end)
        predicate = " AND ".join(where)
        base = f"""
            FROM macro_observations AS observation
            JOIN macro_observation_versions AS version
              ON version.version_id=observation.current_version_id
            JOIN macro_series AS series
              ON series.series_id=version.series_id
            WHERE {predicate}
        """
        facility_order = "CASE series.provider_series_code " + " ".join(
            f"WHEN '{item}' THEN {ordinal}"
            for ordinal, item in enumerate(_REPO_FACILITY_CODES, start=1)
        ) + " ELSE 99 END"
        columns = (
            "effective_date",
            "facility",
            "title",
            "accepted_usd",
            "correction",
            "available_at",
            "captured_at",
        )
        sql = (
            """
            SELECT version.period_start AS effective_date,
                   series.provider_series_code AS facility,
                   series.title,
                   version.value_text AS accepted_usd,
                   version.correction_sequence AS correction,
                   version.available_at,
                   version.captured_at
            """
            + base
            + f"""
            ORDER BY version.period_start {direction.upper()},
                     {facility_order} ASC
            LIMIT ? OFFSET ?
            """
        )
        with _immutable_store_connection(
            self._stores.macro, expected_role="macro"
        ) as connection:
            total = int(
                connection.execute(
                    "SELECT COUNT(*) " + base, tuple(parameters)
                ).fetchone()[0]
            )
            rows = _rows(
                connection.execute(
                    sql,
                    (*parameters, limit, (page - 1) * limit),
                ).fetchall()
            )
        return _result(
            columns,
            rows,
            total,
            page,
            limit,
            {"facility": facility, "start_date": start, "end_date": end},
        )

    def _eia_petroleum_weekly(
        self,
        query: Mapping[str, str],
        direction: str,
        page: int,
        limit: int,
    ) -> dict[str, Any]:
        start = _optional_date(query.get("start_date"), "/start_date")
        end = _optional_date(query.get("end_date"), "/end_date")
        if start and end and start > end:
            raise ValidationError("EIA weekly petroleum date range is invalid")
        where = ["version.canonical_series_id=?"]
        parameters: list[object] = [EIA_WEEKLY_CANONICAL_SERIES_ID]
        if start:
            where.append("version.period>=?")
            parameters.append(start)
        if end:
            where.append("version.period<=?")
            parameters.append(end)
        base = f"""
            FROM stage11_eia_weekly_observations AS observation
            JOIN stage11_eia_weekly_observation_versions AS version
              ON version.version_id=observation.current_version_id
            WHERE {' AND '.join(where)}
        """
        columns = (
            "period", "series", "provider_series", "value", "unit",
            "correction", "available_at", "captured_at",
        )
        sql = (
            """
            SELECT version.period,
                   version.canonical_series_id AS series,
                   version.provider_series_id AS provider_series,
                   version.value_text AS value,
                   version.unit,
                   version.correction_sequence AS correction,
                   version.available_at,
                   version.captured_at
            """
            + base
            + f"""
            ORDER BY version.period {direction.upper()}
            LIMIT ? OFFSET ?
            """
        )
        with _immutable_store_connection(
            self._stores.macro, expected_role="macro"
        ) as connection:
            total = int(
                connection.execute(
                    "SELECT COUNT(*) " + base, tuple(parameters)
                ).fetchone()[0]
            )
            rows = _rows(
                connection.execute(
                    sql,
                    (*parameters, limit, (page - 1) * limit),
                ).fetchall()
            )
        return _result(
            columns,
            rows,
            total,
            page,
            limit,
            {"start_date": start, "end_date": end},
        )

    def _eia_electricity_retail(
        self,
        query: Mapping[str, str],
        direction: str,
        page: int,
        limit: int,
    ) -> dict[str, Any]:
        series_id = query.get("series", "")
        if series_id and series_id not in _EIA_RETAIL_SERIES_IDS:
            raise ValidationError("EIA electricity-retail series is invalid")
        start = query.get("start_period", "")
        end = query.get("end_period", "")
        monthly = re.compile(r"^[0-9]{4}-(0[1-9]|1[0-2])$")
        if start and monthly.fullmatch(start) is None:
            raise ValidationError("EIA electricity-retail start period is invalid")
        if end and monthly.fullmatch(end) is None:
            raise ValidationError("EIA electricity-retail end period is invalid")
        if start and end and start > end:
            raise ValidationError("EIA electricity-retail period range is invalid")

        placeholders = ", ".join("?" for _ in _EIA_RETAIL_SERIES_IDS)
        where = [
            f"version.canonical_series_id IN ({placeholders})",
            "version.state_id='US'",
            "version.sector_id='ALL'",
        ]
        parameters: list[object] = list(_EIA_RETAIL_SERIES_IDS)
        if series_id:
            where.append("version.canonical_series_id=?")
            parameters.append(series_id)
        if start:
            where.append("version.period>=?")
            parameters.append(start)
        if end:
            where.append("version.period<=?")
            parameters.append(end)
        predicate = " AND ".join(where)
        base = f"""
            FROM stage11_eia_retail_observations AS observation
            JOIN stage11_eia_retail_observation_versions AS version
              ON version.version_id=observation.current_version_id
            WHERE {predicate}
        """
        series_order = "CASE version.canonical_series_id " + " ".join(
            f"WHEN '{item}' THEN {ordinal}"
            for ordinal, item in enumerate(
                _EIA_RETAIL_SERIES_IDS, start=1
            )
        ) + " ELSE 99 END"
        columns = (
            "period",
            "series",
            "metric",
            "value",
            "unit",
            "state_id",
            "sector_id",
            "correction",
            "available_at",
            "captured_at",
        )
        sql = (
            """
            SELECT version.period,
                   version.canonical_series_id AS series,
                   version.metric,
                   version.value_text AS value,
                   version.unit,
                   version.state_id,
                   version.sector_id,
                   version.correction_sequence AS correction,
                   version.available_at,
                   version.captured_at
            """
            + base
            + f"""
            ORDER BY version.period {direction.upper()},
                     {series_order} ASC
            LIMIT ? OFFSET ?
            """
        )
        with _immutable_store_connection(
            self._stores.macro, expected_role="macro"
        ) as connection:
            total = int(
                connection.execute(
                    "SELECT COUNT(*) " + base, tuple(parameters)
                ).fetchone()[0]
            )
            rows = _rows(
                connection.execute(
                    sql,
                    (*parameters, limit, (page - 1) * limit),
                ).fetchall()
            )
        return _result(
            columns,
            rows,
            total,
            page,
            limit,
            {
                "series": series_id,
                "start_period": start,
                "end_period": end,
            },
        )

    def _official_series(
        self,
        view: str,
        query: Mapping[str, str],
        direction: str,
        page: int,
        limit: int,
    ) -> dict[str, Any]:
        provider, manifest = _OFFICIAL_VIEW_MANIFESTS[view]
        series_id = query.get("series", "")
        series_ids = tuple(item.series_id for item in manifest)
        if series_id and series_id not in series_ids:
            raise ValidationError("Official macro series is invalid")
        start = _optional_date(query.get("start_date"), "/start_date")
        end = _optional_date(query.get("end_date"), "/end_date")
        if start and end and start > end:
            raise ValidationError("Official macro date range is invalid")

        placeholders = ", ".join("?" for _ in series_ids)
        where = [
            "series.provider=?",
            f"series.series_id IN ({placeholders})",
        ]
        parameters: list[object] = [provider, *series_ids]
        if series_id:
            where.append("series.series_id=?")
            parameters.append(series_id)
        if start:
            where.append("version.period_start>=?")
            parameters.append(start)
        if end:
            where.append("version.period_start<=?")
            parameters.append(end)
        predicate = " AND ".join(where)
        base = f"""
            FROM macro_observations AS observation
            JOIN macro_observation_versions AS version
              ON version.version_id=observation.current_version_id
            JOIN macro_series AS series
              ON series.series_id=version.series_id
            WHERE {predicate}
        """
        series_order = "CASE series.series_id " + " ".join(
            f"WHEN '{item}' THEN {ordinal}"
            for ordinal, item in enumerate(series_ids, start=1)
        ) + " ELSE 99 END"
        columns = (
            "period_start",
            "period_end",
            "series",
            "title",
            "value",
            "unit",
            "missing_reason",
            "correction",
            "available_at",
            "captured_at",
        )
        sql = (
            """
            SELECT version.period_start,
                   version.period_end,
                   series.series_id AS series,
                   series.title,
                   version.value_text AS value,
                   version.unit,
                   version.missing_reason,
                   version.correction_sequence AS correction,
                   version.available_at,
                   version.captured_at
            """
            + base
            + f"""
            ORDER BY version.period_start {direction.upper()},
                     {series_order} ASC
            LIMIT ? OFFSET ?
            """
        )
        with _immutable_store_connection(
            self._stores.macro, expected_role="macro"
        ) as connection:
            total = int(
                connection.execute(
                    "SELECT COUNT(*) " + base,
                    tuple(parameters),
                ).fetchone()[0]
            )
            rows = _rows(
                connection.execute(
                    sql,
                    (*parameters, limit, (page - 1) * limit),
                ).fetchall()
            )
        return _result(
            columns,
            rows,
            total,
            page,
            limit,
            {
                "series": series_id,
                "start_date": start,
                "end_date": end,
            },
        )

    def _soma_summary(
        self, query: Mapping[str, str], direction: str, page: int, limit: int
    ) -> dict[str, Any]:
        component = query.get("component", "")
        if component and component not in _SOMA_COMPONENTS:
            raise ValidationError("SOMA component is invalid")
        start = _optional_date(query.get("start_date"), "/start_date")
        end = _optional_date(query.get("end_date"), "/end_date")
        if start and end and start > end:
            raise ValidationError("SOMA date range is invalid")

        where = ["ranked.snapshot_rank=1"]
        parameters: list[object] = []
        if component:
            where.append("component.category=?")
            parameters.append(component)
        if start:
            where.append("component.as_of_date>=?")
            parameters.append(start)
        if end:
            where.append("component.as_of_date<=?")
            parameters.append(end)
        predicate = " AND ".join(where)
        latest = """
            WITH ranked AS (
                SELECT snapshot.snapshot_id, snapshot.as_of_date,
                       snapshot.captured_at,
                       ROW_NUMBER() OVER (
                           PARTITION BY snapshot.as_of_date
                           ORDER BY snapshot.captured_at DESC,
                                    snapshot.snapshot_id DESC
                       ) AS snapshot_rank
                FROM soma_snapshots AS snapshot
                JOIN ingestion_runs AS run ON run.run_id=snapshot.run_id
                WHERE snapshot.completeness='complete'
                  AND run.status='succeeded'
            )
        """
        base = f"""
            FROM ranked
            JOIN soma_summary_components AS component
              ON component.snapshot_id=ranked.snapshot_id
            WHERE {predicate}
        """
        component_order = "CASE component.category " + " ".join(
            f"WHEN '{item}' THEN {ordinal}"
            for ordinal, item in enumerate(_SOMA_COMPONENTS, start=1)
        ) + " ELSE 99 END"
        columns = (
            "as_of_date",
            "component",
            "amount_thousands_usd",
            "missing_reason",
            "measure",
            "unit",
            "available_at",
            "captured_at",
        )
        sql = (
            latest
            + """
            SELECT component.as_of_date,
                   component.category AS component,
                   component.value_text AS amount_thousands_usd,
                   component.missing_reason,
                   component.measure,
                   component.unit,
                   component.available_at,
                   ranked.captured_at
            """
            + base
            + f"""
            ORDER BY component.as_of_date {direction.upper()},
                     {component_order} ASC
            LIMIT ? OFFSET ?
            """
        )
        with _immutable_store_connection(
            self._stores.macro, expected_role="macro"
        ) as connection:
            total = int(
                connection.execute(
                    latest + "SELECT COUNT(*) " + base,
                    tuple(parameters),
                ).fetchone()[0]
            )
            rows = _rows(
                connection.execute(
                    sql,
                    (*parameters, limit, (page - 1) * limit),
                ).fetchall()
            )
        return _result(
            columns,
            rows,
            total,
            page,
            limit,
            {"component": component, "start_date": start, "end_date": end},
        )

    def _fmp_economic_calendar(
        self, query: Mapping[str, str], direction: str, page: int, limit: int
    ) -> dict[str, Any]:
        mode = query.get("mode", "current")
        if mode not in {"current", "history"}:
            raise ValidationError("FMP calendar mode is invalid")
        country = _text_filter(query.get("country"), "/country", 64)
        priority = _text_filter(query.get("priority"), "/priority", 16)
        if priority and priority not in _FMP_PRIORITIES:
            raise ValidationError("FMP calendar priority is invalid")
        event_name = _text_filter(query.get("event_name"), "/event_name", 256)
        keyword = _text_filter(query.get("keyword"), "/keyword", 128)
        start = _optional_date(query.get("start_date"), "/start_date")
        end = _optional_date(query.get("end_date"), "/end_date")
        if start and end and start > end:
            raise ValidationError("FMP calendar date range is invalid")

        columns = (
            "event_at", "country", "priority", "event_name", "currency", "unit",
            "previous", "estimate", "actual", "change", "change_percentage",
            "captured_at", "request_start", "request_end", "capture_id",
            "source_row", "row_sha256", "response_sha256", "semantic_identity",
            "raw_row_json",
        )

        if mode == "current":
            with read_connection(self._stores, StoreRole.MACRO) as connection:
                cache = connection.execute(
                    """
                    SELECT cache.response_bytes, cache.response_sha256,
                           cache.semantic_identity,
                           cache.source_semantic_identity, cache.captured_at,
                           cache.captured_precision,
                           cache.request_start_date, cache.request_end_date,
                           cache.normalization_version,
                           cache.persistence_version, cache.row_count,
                           cache.receipt_id,
                           receipt.response_sha256 AS receipt_response_sha256,
                           receipt.semantic_identity AS receipt_semantic_identity,
                           receipt.source_semantic_identity
                               AS receipt_source_semantic_identity,
                           receipt.captured_at AS receipt_captured_at,
                           receipt.row_count AS receipt_row_count
                    FROM fmp_economic_calendar_latest_response_cache AS cache
                    JOIN fmp_economic_calendar_fetch_receipts AS receipt
                      ON receipt.receipt_id=cache.receipt_id
                    JOIN ingestion_runs AS run ON run.run_id=receipt.run_id
                    WHERE cache.feed_id='fmp_us'
                      AND run.dataset_id=
                          'macro.fmp.economic_calendar_incremental_evidence'
                      AND run.command=
                          'fmp.macro.us_economic_calendar_wholesale'
                      AND run.status='succeeded'
                    """
                ).fetchone()
            if cache is not None:
                capture = parse_fmp_us_calendar_wholesale(
                    bytes(cache["response_bytes"]),
                    captured_at=str(cache["captured_at"]),
                    start_date=str(cache["request_start_date"]),
                    end_date=str(cache["request_end_date"]),
                )
                if (
                    str(cache["response_sha256"]) != capture.response_sha256
                    or str(cache["source_semantic_identity"])
                    != capture.semantic_identity
                    or str(cache["captured_precision"])
                    != capture.captured_precision
                    or str(cache["normalization_version"])
                    != "fmp_us_calendar_wholesale_evidence_v1"
                    or str(cache["persistence_version"])
                    != "fmp_us_calendar_incremental_events_v2"
                    or int(cache["row_count"]) != len(capture.rows)
                    or str(cache["receipt_response_sha256"])
                    != capture.response_sha256
                    or str(cache["receipt_semantic_identity"])
                    != str(cache["semantic_identity"])
                    or str(cache["receipt_source_semantic_identity"])
                    != capture.semantic_identity
                    or str(cache["receipt_captured_at"]) != capture.captured_at
                    or int(cache["receipt_row_count"]) != len(capture.rows)
                ):
                    raise StoreUnavailableError(
                        "FMP calendar latest response cache is invalid"
                    )
                rows = []
                for item in capture.rows:
                    row = {
                        "event_at": item.event_at,
                        "country": item.country,
                        "priority": _stored_json_value(item.impact_json),
                        "event_name": item.event_name,
                        "currency": item.currency,
                        "unit": item.unit,
                        "previous": _stored_json_value(item.previous_json),
                        "estimate": _stored_json_value(item.estimate_json),
                        "actual": _stored_json_value(item.actual_json),
                        "change": _stored_json_value(item.change_json),
                        "change_percentage": _stored_json_value(
                            item.change_percentage_json
                        ),
                        "captured_at": capture.captured_at,
                        "request_start": capture.request_start_date,
                        "request_end": capture.request_end_date,
                        "capture_id": str(cache["receipt_id"]),
                        "source_row": item.source_row,
                        "row_sha256": item.row_sha256,
                        "response_sha256": capture.response_sha256,
                        "semantic_identity": capture.semantic_identity,
                        "raw_row_json": item.raw_row_json,
                    }
                    keyword_text = " ".join(
                        value
                        for value in (
                            item.event_name,
                            item.country,
                            item.currency or "",
                            item.unit or "",
                            item.raw_row_json,
                        )
                        if value
                    ).casefold()
                    if country and item.country.casefold() != country.casefold():
                        continue
                    if priority and row["priority"] != priority:
                        continue
                    if (
                        event_name
                        and event_name.casefold() not in item.event_name.casefold()
                    ):
                        continue
                    if keyword and keyword.casefold() not in keyword_text:
                        continue
                    if start and item.event_at[:10] < start:
                        continue
                    if end and item.event_at[:10] > end:
                        continue
                    rows.append(row)
                rows.sort(
                    key=lambda item: (
                        str(item["event_at"]),
                        str(item["captured_at"]),
                        str(item["capture_id"]),
                        int(item["source_row"]),
                    ),
                    reverse=direction == "desc",
                )
                total = len(rows)
                offset = (page - 1) * limit
                return _result(
                    columns,
                    rows[offset : offset + limit],
                    total,
                    page,
                    limit,
                    {
                        "mode": mode,
                        "country": country,
                        "priority": priority,
                        "event_name": event_name,
                        "keyword": keyword,
                        "start_date": start,
                        "end_date": end,
                    },
                )

        where: list[str] = []
        parameters: list[object] = []
        if country:
            where.append("LOWER(calendar.country)=LOWER(?)")
            parameters.append(country)
        if priority:
            where.append("calendar.priority=?")
            parameters.append(dumps_strict(priority))
        if event_name:
            where.append("calendar.event_name LIKE ? ESCAPE '\\'")
            parameters.append(_like_contains(event_name))
        if keyword:
            pattern = _like_contains(keyword)
            where.append(
                "(calendar.event_name LIKE ? ESCAPE '\\' "
                "OR calendar.country LIKE ? ESCAPE '\\' "
                "OR COALESCE(calendar.currency, '') LIKE ? ESCAPE '\\' "
                "OR COALESCE(calendar.unit, '') LIKE ? ESCAPE '\\' "
                "OR calendar.raw_row_json LIKE ? ESCAPE '\\')"
            )
            parameters.extend((pattern, pattern, pattern, pattern, pattern))
        if start:
            where.append("SUBSTR(calendar.event_at, 1, 10)>=?")
            parameters.append(start)
        if end:
            where.append("SUBSTR(calendar.event_at, 1, 10)<=?")
            parameters.append(end)

        legacy = """
            SELECT row.event_at, row.country, row.impact_json AS priority,
                   row.event_name, row.currency, row.unit,
                   row.previous_json AS previous,
                   row.estimate_json AS estimate,
                   row.actual_json AS actual,
                   row.change_json AS change,
                   row.change_percentage_json AS change_percentage,
                   capture.captured_at,
                   capture.request_start_date AS request_start,
                   capture.request_end_date AS request_end,
                   row.capture_id, row.source_row, row.row_sha256,
                   capture.response_sha256, capture.semantic_identity,
                   row.raw_row_json,
                   LAG(row.row_sha256) OVER (
                       PARTITION BY row.country, row.event_at, row.event_name,
                                    COALESCE(row.currency, '')
                       ORDER BY capture.captured_at, row.capture_id,
                                row.source_row
                   ) AS previous_row_sha256,
                   ROW_NUMBER() OVER (
                       PARTITION BY row.country, row.event_at, row.event_name,
                                    COALESCE(row.currency, '')
                       ORDER BY capture.captured_at DESC, row.capture_id DESC,
                                row.source_row DESC
                   ) AS current_rank
            FROM fmp_economic_calendar_rows AS row
            JOIN fmp_economic_calendar_captures AS capture
              ON capture.capture_id=row.capture_id
            JOIN ingestion_runs AS run ON run.run_id=capture.run_id
            JOIN ingestion_artifacts AS artifact
              ON artifact.artifact_id=capture.artifact_id
             AND artifact.run_id=run.run_id
            JOIN ingestion_snapshots AS snapshot
              ON snapshot.snapshot_id=capture.snapshot_id
             AND snapshot.run_id=run.run_id
            WHERE run.dataset_id='macro.fmp.economic_calendar_evidence'
              AND run.command='fmp.macro.us_economic_calendar_wholesale'
              AND run.status='succeeded'
              AND artifact.dataset_id='macro.fmp.economic_calendar_evidence'
              AND artifact.content_sha256=capture.response_sha256
              AND snapshot.dataset_id='macro.fmp.economic_calendar_evidence'
              AND snapshot.semantic_identity=capture.semantic_identity
              AND snapshot.completeness='complete'
              AND snapshot.validation_state='validated'
        """
        if mode == "current":
            common_table = f"WITH legacy AS ({legacy})"
            where.insert(0, "calendar.current_rank=1")
            relation = "legacy"
        else:
            common_table = f"""
                WITH legacy AS ({legacy}),
                new_history AS (
                    SELECT event.event_at, event.country,
                           version.impact_json AS priority,
                           event.event_name, version.currency, version.unit,
                           version.previous_json AS previous,
                           version.estimate_json AS estimate,
                           version.actual_json AS actual,
                           version.change_json AS change,
                           version.change_percentage_json AS change_percentage,
                           version.captured_at,
                           receipt.request_start_date AS request_start,
                           receipt.request_end_date AS request_end,
                           receipt.receipt_id AS capture_id,
                           version.source_row, version.row_sha256,
                           receipt.response_sha256,
                           receipt.source_semantic_identity AS semantic_identity,
                           version.raw_row_json,
                           NULL AS previous_row_sha256,
                           version.correction_sequence AS current_rank
                    FROM fmp_economic_calendar_raw_event_versions AS version
                    JOIN fmp_economic_calendar_raw_events AS event
                      ON event.event_id=version.event_id
                    JOIN fmp_economic_calendar_fetch_receipts AS receipt
                      ON receipt.receipt_id=version.receipt_id
                    JOIN ingestion_runs AS run ON run.run_id=receipt.run_id
                    WHERE run.dataset_id=
                              'macro.fmp.economic_calendar_incremental_evidence'
                      AND run.command=
                              'fmp.macro.us_economic_calendar_wholesale'
                      AND run.status='succeeded'
                ),
                calendar_history AS (
                    SELECT * FROM legacy
                    WHERE previous_row_sha256 IS NULL
                       OR previous_row_sha256 != row_sha256
                    UNION ALL
                    SELECT * FROM new_history
                )
            """
            relation = "calendar_history"
        predicate = "" if not where else "WHERE " + " AND ".join(where)
        base = f"FROM {relation} AS calendar {predicate}"
        sql = f"""
            {common_table}
            SELECT event_at, country, priority, event_name, currency, unit,
                   previous, estimate, actual, change, change_percentage,
                   captured_at, request_start, request_end, capture_id,
                   source_row, row_sha256, response_sha256,
                   semantic_identity, raw_row_json
            {base}
            ORDER BY event_at {direction.upper()},
                     captured_at {direction.upper()},
                     capture_id {direction.upper()},
                     source_row {direction.upper()}
            LIMIT ? OFFSET ?
        """
        with read_connection(self._stores, StoreRole.MACRO) as connection:
            total = int(
                connection.execute(
                    f"{common_table} SELECT COUNT(*) {base}", tuple(parameters)
                ).fetchone()[0]
            )
            stored = _rows(
                connection.execute(
                    sql, (*parameters, limit, (page - 1) * limit)
                ).fetchall()
            )
        rows: list[dict[str, Any]] = []
        for stored_row in stored:
            row = dict(stored_row)
            for field in (
                "priority", "previous", "estimate", "actual", "change",
                "change_percentage",
            ):
                row[field] = _stored_json_value(row[field])
            rows.append(row)
        return _result(
            columns,
            rows,
            total,
            page,
            limit,
            {
                "mode": mode,
                "country": country,
                "priority": priority,
                "event_name": event_name,
                "keyword": keyword,
                "start_date": start,
                "end_date": end,
            },
        )


class CanonicalInspectorApplication(Stage1Application):
    """Read-only HTML/JSON surface for fixed views and public tool calls."""

    def __init__(self, store_map: StoreMap, registry: Registry) -> None:
        if registry.registry_version != _CURRENT_REGISTRY or registry.schema_version != _CURRENT_SCHEMA:
            raise ValidationError("Canonical inspector requires the current registry")
        super().__init__(store_map, registry)
        self._reads = CanonicalInspectorReadService(store_map, registry)
        css = (_ASSET_ROOT / "dashboard.css").read_bytes()
        font = (_ASSET_ROOT / "inter-variable.woff2").read_bytes()
        if hashlib.sha256(font).hexdigest() != INTER_FONT_SHA256:
            raise StoreUnavailableError("Canonical inspector local assets are invalid")
        self._assets = {
            "/assets/dashboard.css": (css, "text/css; charset=utf-8"),
            "/assets/inter-variable.woff2": (font, "font/woff2"),
            "/assets/inspector-tools.css": (
                (_ASSET_ROOT / "inspector_tools.css").read_bytes(),
                "text/css; charset=utf-8",
            ),
            "/assets/inspector-tools.js": (
                (_ASSET_ROOT / "inspector_tools.js").read_bytes(),
                "application/javascript; charset=utf-8",
            ),
        }

    def _handle_get(
        self, path: str, query: Mapping[str, str], headers: Mapping[str, str]
    ) -> HttpResponse:
        del headers
        asset = self._assets.get(path)
        if asset is not None:
            if query:
                raise ValidationError("Inspector assets do not accept query fields")
            return HttpResponse(HTTPStatus.OK, asset[0], asset[1])
        if path == "/healthz":
            if query:
                raise ValidationError("Inspector liveness does not accept query fields")
            return self._json_response(
                HTTPStatus.OK,
                self._generic_success(
                    {
                        "status": "ok",
                        "service": "quant-data-inspector",
                        "scope": "process_liveness_only",
                        "data_freshness": "not_checked",
                        "provider_health": "not_checked",
                        "scheduler_health": "not_checked",
                    },
                    route="healthz",
                ),
            )
        if path == "/api/rows":
            result = self._reads.inspect(query)
            return self._json_response(
                HTTPStatus.OK,
                self._generic_success(result, route="canonical_inspector"),
            )
        if path in {"/data-status", "/api/data-status"}:
            if query:
                raise ValidationError("Data status does not accept query fields")
            try:
                result = self.dispatcher.call(
                    "data.get_dataset_status",
                    {
                        "stores": [],
                        "dataset_ids": [],
                        "statuses": [],
                        "limit": 128,
                    },
                    tool_version=_CURRENT_DATA_STATUS_TOOL_VERSION,
                )
                error = None
            except QuantDataError as exc:
                result = {}
                error = exc.safe_message
            if path == "/api/data-status":
                if error is not None:
                    raise StoreUnavailableError(error)
                return self._json_response(
                    HTTPStatus.OK,
                    self._generic_success(result, route="data_status"),
                )
            return HttpResponse(
                HTTPStatus.OK,
                render_data_status_page(
                    result,
                    registry_revision=self._registry.revision,
                    error=error,
                ).encode("utf-8"),
                "text/html; charset=utf-8",
            )
        if path == "/api/agent-tools":
            return super()._handle_get(path, query, {})
        if path == "/news":
            status_result: Mapping[str, Any] = {}
            status_error: str | None = None
            try:
                arguments = _current_news_arguments(query)
                result = self.dispatcher.call(
                    "news.search",
                    arguments,
                    tool_version=_CURRENT_NEWS_TOOL_VERSION,
                )
                error = None
            except QuantDataError as exc:
                result = {}
                error = exc.safe_message
            try:
                status_result = self.dispatcher.call(
                    "news.get_source_status",
                    {"source_ids": []},
                    tool_version="1.0.0",
                )
            except QuantDataError as exc:
                status_error = exc.safe_message
            return HttpResponse(
                HTTPStatus.OK,
                _render_current_news_page(
                    result,
                    query,
                    self._registry.revision,
                    error,
                    status_result,
                    status_error,
                ).encode("utf-8"),
                "text/html; charset=utf-8",
            )
        if path == "/agent-tools":
            unknown = sorted(set(query) - {"tool", "tool_version"})
            if unknown:
                raise ValidationError(
                    "Agent Tools query contains an unsupported field",
                    issues=(
                        Issue(
                            f"/{unknown[0]}",
                            "additional_properties",
                            "Field is not supported",
                        ),
                    ),
                )
            tool = query.get("tool")
            tool_version = query.get("tool_version")
            manifest = self.dispatcher.manifest()
            names = {
                item.get("name")
                for item in manifest.get("tools", ())
                if isinstance(item, Mapping)
            }
            if tool is not None and tool not in names:
                raise ValidationError("Agent Tools selection is invalid")
            if tool_version is not None:
                if tool is None:
                    raise ValidationError(
                        "Agent Tools version requires a selected tool"
                    )
                declaration = next(
                    item
                    for item in manifest["tools"]
                    if isinstance(item, Mapping) and item.get("name") == tool
                )
                if tool_version != latest_tool_version(declaration):
                    raise ValidationError(
                        "Agent Tools exposes only the latest tool version"
                    )
            return HttpResponse(
                HTTPStatus.OK,
                render_current_agent_tools_page(
                    manifest,
                    self._registry,
                    requested_tool=tool,
                ).encode("utf-8"),
                "text/html; charset=utf-8",
            )
        if path == "/":
            try:
                result = self._reads.inspect(query)
                error = None
            except (ValidationError, ResourceLimitError, StoreUnavailableError) as exc:
                fallback_view = query.get("view", "market-prices")
                if fallback_view not in _VIEWS:
                    fallback_view = "market-prices"
                result = {
                    "view": fallback_view,
                    "columns": (),
                    "rows": (),
                    "total": 0,
                    "page": 1,
                    "limit": 25,
                    "direction": "desc",
                    "query": {},
                    "truncated": False,
                }
                error = exc.safe_message
            return HttpResponse(
                HTTPStatus.OK,
                _render_page(result, query, self._registry.revision, error).encode("utf-8"),
                "text/html; charset=utf-8",
            )
        raise RouteNotFoundError("Route was not found")

    def _handle_post(
        self,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        body: bytes,
    ) -> HttpResponse:
        if path == "/api/agent-tools/call":
            return super()._handle_post(path, query, headers, body)
        del query, headers, body
        if path in {
            "/",
            "/healthz",
            "/data-status",
            "/api/data-status",
            "/news",
            "/agent-tools",
            "/api/rows",
            "/api/agent-tools",
            *self._assets,
        }:
            raise MethodNotAllowedError("HTTP method is not supported")
        raise RouteNotFoundError("Route was not found")


def build_canonical_inspector(project_root: str | Path) -> CanonicalInspectorApplication:
    """Build the inspector from the current registry and exact canonical paths."""

    root = Path(project_root).expanduser().resolve(strict=True)
    registry = load_registry(
        CANONICAL_REGISTRY_PATH,
        project_root=root,
        environment={},
    )
    stores = resolve_store_map(registry, project_root=root, environment={})
    for role in (
        StoreRole.MARKET,
        StoreRole.MACRO,
        StoreRole.COMPANY,
        StoreRole.NEWS,
    ):
        expected = root / "data" / f"{role.value}.sqlite"
        if expected.is_symlink() or not expected.is_file():
            raise StoreUnavailableError(f"Canonical {role.value} store is unavailable")
        if stores.path(role) != expected.resolve(strict=True):
            raise ValidationError("Canonical inspector store binding is invalid")
    return CanonicalInspectorApplication(stores, registry)


@contextmanager
def _immutable_store_connection(
    path: Path, *, expected_role: str
) -> Iterator[sqlite3.Connection]:
    """Open one quiet canonical store without touching its WAL/SHM state."""

    if expected_role not in {"market", "macro", "company"}:
        raise ValidationError("Canonical inspector store role is invalid")
    if path.is_symlink() or not path.is_file():
        raise StoreUnavailableError("Canonical store is unavailable")
    before_sidecars = {name: _sidecar_stamp(path, name) for name in ("wal", "shm", "journal")}
    for name in ("wal", "journal"):
        stamp = before_sidecars[name]
        if stamp is not None and stamp[4] != 0:
            raise StoreUnavailableError("Canonical store is not quiet")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise StoreUnavailableError("Canonical store is unavailable") from exc
    connection: sqlite3.Connection | None = None
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise StoreUnavailableError("Canonical store identity is invalid")
        current = os.stat(path, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
            raise StoreUnavailableError("Canonical store identity changed")
        uri = f"file:/proc/self/fd/{descriptor}?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True, timeout=0.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        if connection.execute("PRAGMA query_only").fetchone()[0] != 1:
            raise StoreUnavailableError("Canonical connection is not query-only")
        connection.execute("BEGIN")
        role = connection.execute(
            "SELECT store_role FROM store_metadata WHERE singleton=1"
        ).fetchone()
        if role is None or role[0] != expected_role:
            raise StoreUnavailableError("Canonical store has the wrong role")
        yield connection
    except sqlite3.Error as exc:
        raise StoreUnavailableError("Canonical store is unavailable") from exc
    finally:
        if connection is not None:
            if connection.in_transaction:
                connection.rollback()
            connection.close()
        try:
            after = os.fstat(descriptor)
            stable_before = (
                before.st_dev, before.st_ino, before.st_mode, before.st_nlink,
                before.st_size, before.st_mtime_ns, before.st_ctime_ns,
            )
            stable_after = (
                after.st_dev, after.st_ino, after.st_mode, after.st_nlink,
                after.st_size, after.st_mtime_ns, after.st_ctime_ns,
            )
            if stable_before != stable_after:
                raise StoreUnavailableError("Canonical store changed during inspection")
            if any(_sidecar_stamp(path, name) != before_sidecars[name] for name in before_sidecars):
                raise StoreUnavailableError("Canonical store sidecar changed during inspection")
        finally:
            os.close(descriptor)


def _sidecar_stamp(path: Path, name: str) -> tuple[int, ...] | None:
    suffix = {"wal": "-wal", "shm": "-shm", "journal": "-journal"}[name]
    candidate = Path(str(path) + suffix)
    try:
        value = os.stat(candidate, follow_symlinks=False)
    except FileNotFoundError:
        return None
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _bounded_int(raw: str, pointer: str, minimum: int, maximum: int) -> int:
    if not raw.isascii() or not raw.isdecimal():
        raise ValidationError("Inspector pagination is invalid")
    value = int(raw)
    if value < minimum or value > maximum:
        raise ResourceLimitError(
            "Inspector pagination exceeds its fixed bound",
            issues=(Issue(pointer, "range", f"Expected {minimum} through {maximum}"),),
        )
    return value


def _optional_date(raw: str | None, pointer: str) -> str | None:
    if raw in (None, ""):
        return None
    try:
        return date.fromisoformat(raw).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            "Inspector date is invalid",
            issues=(Issue(pointer, "format", "Expected YYYY-MM-DD"),),
        ) from exc


def _text_filter(raw: str | None, pointer: str, maximum: int) -> str:
    value = "" if raw is None else raw.strip()
    if len(value) > maximum or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValidationError(
            "Inspector text filter is invalid",
            issues=(Issue(pointer, "format", f"Expected at most {maximum} printable characters"),),
        )
    return value


def _like_contains(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _option_target_dtes(raw: Any) -> list[int]:
    values = loads_strict(str(raw), max_bytes=1024)
    if (
        not isinstance(values, list)
        or not values
        or any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or value not in _OPTIONS_SURFACE_TARGET_DTES
            for value in values
        )
        or values != sorted(set(values))
    ):
        raise StoreUnavailableError("Option capture scope is invalid")
    return values


def _stored_json_value(raw: Any) -> Any:
    if raw is None:
        return None
    value = loads_strict(str(raw), max_bytes=4096)
    if isinstance(value, (dict, list)):
        return dumps_strict(value, max_bytes=4096)
    return value


def _rows(values: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(value) for value in values]


def _result(
    columns: tuple[str, ...],
    rows: list[dict[str, Any]],
    total: int,
    page: int,
    limit: int,
    query: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "columns": columns,
        "rows": rows,
        "total": total,
        "truncated": page * limit < total,
        "query": dict(query),
    }


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _current_news_arguments(query: Mapping[str, str]) -> dict[str, Any]:
    unknown = sorted(set(query) - _CURRENT_NEWS_QUERY_FIELDS)
    if unknown:
        raise ValidationError(
            "Current news query contains an unsupported field",
            issues=(
                Issue(
                    f"/{unknown[0]}",
                    "additional_properties",
                    "Field is not supported",
                ),
            ),
        )
    text_query = _text_filter(query.get("query"), "/query", 500)
    symbol = _text_filter(query.get("symbol"), "/symbol", 32).upper()
    if symbol and _SYMBOL.fullmatch(symbol) is None:
        raise ValidationError("Current news symbol is invalid")
    source_id = _text_filter(query.get("source_id"), "/source_id", 64)
    if source_id and source_id not in CURRENT_NEWS_TOOL_SOURCE_IDS:
        raise ValidationError("Current news source is invalid")
    start = _optional_date(query.get("start_date"), "/start_date")
    end = _optional_date(query.get("end_date"), "/end_date")
    if start and end and start > end:
        raise ValidationError("Current news date range is invalid")
    cursor = _text_filter(query.get("cursor"), "/cursor", 4096) or None
    limit = _bounded_int(query.get("limit", "25"), "/limit", 1, _MAX_LIMIT)
    return {
        "query": text_query,
        "symbols": [symbol] if symbol else [],
        "source_ids": [source_id] if source_id else [],
        "mode": "latest",
        "as_of": None,
        "date_only_policy": "completed_date",
        "start_date": start,
        "end_date": end,
        "cursor": cursor,
        "limit": limit,
    }


def _current_news_rows(result: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    records = result.get("records", ())
    if not isinstance(records, Sequence) or isinstance(
        records, (str, bytes, bytearray)
    ):
        return ()
    rendered: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, Mapping):
            continue
        fields = record.get("fields", ())
        if not isinstance(fields, Sequence) or isinstance(
            fields, (str, bytes, bytearray)
        ):
            continue
        row: dict[str, Any] = {}
        for field in fields:
            if not isinstance(field, Mapping):
                continue
            name = field.get("name")
            if isinstance(name, str) and name not in row:
                row[name] = field.get("value")
        rendered.append(row)
    return tuple(rendered)


def _current_news_symbols(value: Any) -> str:
    if not isinstance(value, str):
        return "" if value is None else str(value)
    try:
        decoded = loads_strict(value, max_bytes=4096)
    except QuantDataError:
        return value
    if isinstance(decoded, list) and all(isinstance(item, str) for item in decoded):
        return ", ".join(decoded)
    return value


def _safe_source_url(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 4096:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return value


def _status_object(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, str):
        return value if isinstance(value, Mapping) else {}
    try:
        decoded = loads_strict(value, max_bytes=16_384)
    except QuantDataError:
        return {}
    return decoded if isinstance(decoded, Mapping) else {}


def _status_capture_time(value: Any) -> str:
    captured = _status_object(value).get("captured_at")
    if isinstance(captured, Mapping):
        captured = captured.get("value")
    return captured if isinstance(captured, str) else "—"


def _status_outcome(value: Any) -> str:
    outcome = _status_object(value)
    kind = outcome.get("outcome_kind")
    recorded = outcome.get("recorded_at")
    if isinstance(recorded, Mapping):
        recorded = recorded.get("value")
    if isinstance(kind, str) and isinstance(recorded, str):
        return f"{kind} · {recorded}"
    return kind if isinstance(kind, str) else "—"


def _render_news_status_panel(
    result: Mapping[str, Any], error: str | None
) -> str:
    rows = _current_news_rows(result)
    notice = (
        f'<div class="state-notice warning" role="alert">{html.escape(error)}</div>'
        if error
        else ""
    )
    body = "".join(
        "<tr><th scope=\"row\"><code>"
        + _html_value(row.get("source_id"))
        + "</code></th><td>"
        + _html_value(row.get("provider"))
        + "</td><td>"
        + _html_value(row.get("status"))
        + "</td><td>"
        + _html_value(_status_capture_time(row.get("latest_successful_capture")))
        + "</td><td>"
        + _html_value(_status_outcome(row.get("latest_outcome")))
        + "</td><td>"
        + _html_value(row.get("article_count"))
        + "</td></tr>"
        for row in rows
    )
    if not body:
        body = '<tr><td colspan="6">No retained source status is available.</td></tr>'
    return (
        '<section class="panel"><div class="panel-header"><div>'
        '<p class="panel-kicker">Local retained status · not live provider health</p>'
        '<h2>News source status</h2><p>Latest retained attempts, outcomes, and '
        'successful captures. This does not test credentials, scheduler processes, '
        'or fetch a provider.</p></div><a href="/data-status">All data status</a></div>'
        + notice
        + '<div class="table-scroll"><table><thead><tr><th>Source</th><th>Provider</th>'
        '<th>Latest status</th><th>Last successful capture</th><th>Latest outcome</th>'
        '<th>Articles</th></tr></thead><tbody>'
        + body
        + "</tbody></table></div></section>"
    )


def _render_current_news_page(
    result: Mapping[str, Any],
    raw_query: Mapping[str, str],
    revision: str,
    error: str | None,
    status_result: Mapping[str, Any],
    status_error: str | None,
) -> str:
    navigation = "".join(
        '<a href="/?' + html.escape(urlencode({"view": item}), quote=True) + '">'
        + html.escape(_VIEW_LABELS[item])
        + "</a>"
        for item in _VIEWS
    )
    navigation += (
        '<a href="/news" aria-current="page">Current news</a>'
        '<a href="/data-status">Data status</a>'
        '<a href="/agent-tools">Agent Tools</a>'
    )
    notice = (
        f'<div class="state-notice warning" role="alert">{html.escape(error)}</div>'
        if error
        else ""
    )
    selected_source = raw_query.get("source_id", "")
    source_options = '<option value="">All fixed sources</option>' + "".join(
        '<option value="'
        + html.escape(source_id, quote=True)
        + '"'
        + (" selected" if selected_source == source_id else "")
        + ">"
        + html.escape(_CURRENT_NEWS_SOURCE_LABELS[source_id])
        + "</option>"
        for source_id in CURRENT_NEWS_TOOL_SOURCE_IDS
    )
    selected_limit = raw_query.get("limit", "25")
    limit_options = "".join(
        f'<option value="{limit}"'
        + (" selected" if selected_limit == str(limit) else "")
        + f">{limit}</option>"
        for limit in (10, 25, 50, 100)
    )
    form = (
        '<form class="query-form" method="get" action="/news"><fieldset>'
        '<legend>Current headline filters</legend><div class="form-grid">'
        + _input("query", "Headline or summary", raw_query.get("query"))
        + _input(
            "symbol",
            "Exact symbol",
            raw_query.get("symbol"),
            placeholder="AAPL",
        )
        + f'<label>Source<select name="source_id">{source_options}</select></label>'
        + _input(
            "start_date",
            "Published from",
            raw_query.get("start_date"),
            input_type="date",
        )
        + _input(
            "end_date",
            "Published through",
            raw_query.get("end_date"),
            input_type="date",
        )
        + f'<label>Rows<select name="limit">{limit_options}</select></label>'
        + '</div><div class="form-actions"><button type="submit">Show news</button>'
        '<a href="/news">Clear filters</a></div></fieldset></form>'
    )
    rows = _current_news_rows(result)
    body_parts: list[str] = []
    for row in rows:
        published = row.get("published_normalized_at") or row.get(
            "published_date_raw"
        )
        summary = row.get("summary")
        headline = "<strong>" + _html_value(row.get("headline")) + "</strong>"
        if isinstance(summary, str) and summary:
            headline += "<br><small>" + html.escape(summary) + "</small>"
        source_url = _safe_source_url(row.get("source_url"))
        source_link = (
            '<a href="'
            + html.escape(source_url, quote=True)
            + '" target="_blank" rel="noopener noreferrer">Open source</a>'
            if source_url
            else '<span class="null">—</span>'
        )
        body_parts.append(
            "<tr><td>"
            + _html_value(published)
            + "</td><td>"
            + _html_value(row.get("feed_id"))
            + "</td><td>"
            + headline
            + "</td><td>"
            + _html_value(_current_news_symbols(row.get("symbols")))
            + "</td><td>"
            + _html_value(row.get("available_at"))
            + "</td><td>"
            + source_link
            + "</td></tr>"
        )
    body = "".join(body_parts)
    if not body:
        body = (
            '<tr><td colspan="6">'
            "No retained headlines match this selection."
            "</td></tr>"
        )
    truncation = result.get("truncation", {})
    if not isinstance(truncation, Mapping):
        truncation = {}
    total = truncation.get("total_known_count")
    total_copy = (
        f"{total:,} retained rows match this selection."
        if isinstance(total, int) and not isinstance(total, bool)
        else "Retained rows matching this selection."
    )
    pager_links: list[str] = []
    if raw_query.get("cursor"):
        newest = {
            key: value for key, value in raw_query.items() if key != "cursor"
        }
        pager_links.append(
            '<a href="/news?'
            + html.escape(urlencode(newest), quote=True)
            + '">Newest</a>'
        )
    next_cursor = truncation.get("next_cursor")
    if truncation.get("has_more") is True and isinstance(next_cursor, str):
        following = {
            key: value for key, value in raw_query.items() if key != "cursor"
        }
        following["cursor"] = next_cursor
        pager_links.append(
            '<a href="/news?'
            + html.escape(urlencode(following), quote=True)
            + '">Next</a>'
        )
    pager = (
        '<div class="form-actions">' + " · ".join(pager_links) + "</div>"
        if pager_links
        else ""
    )
    status_panel = _render_news_status_panel(status_result, status_error)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Current news · Canonical Data Inspector</title><link rel="stylesheet" href="/assets/dashboard.css"></head>
<body><a class="skip-link" href="#main-content">Skip to content</a><div class="app-shell">
<header class="site-header"><a class="brand" href="/"><span class="brand-mark">QD</span><strong>Canonical Data Inspector</strong></a>
<p class="shell-status"><span aria-hidden="true">●</span> Local read-only · registry {html.escape(revision)}</p></header>
<nav class="primary-nav" aria-label="Inspector views">{navigation}</nav>
<main id="main-content"><section class="page-intro"><p class="eyebrow">Retained headlines · inspection only</p>
<h1>Current news</h1><p>Browse bounded headline metadata from the fixed canonical news database. Article bodies, raw provider responses, database paths, writes, and live provider calls are not exposed.</p></section>
{notice}{status_panel}<section class="panel"><div class="panel-header"><div><p class="panel-kicker">Filters</p><h2>Choose what to inspect</h2></div></div>{form}</section>
<section class="panel"><div class="panel-header"><div><p class="panel-kicker">Latest retained metadata</p><h2>{len(rows):,} headlines on this page</h2><p>{html.escape(total_copy)}</p></div>
<a href="/agent-tools?tool=news.search">Advanced tool view</a></div>
<div class="table-scroll"><table><thead><tr><th>Published</th><th>Source</th><th>Headline</th><th>Symbols</th><th>Available locally</th><th>Link</th></tr></thead><tbody>{body}</tbody></table></div>{pager}</section>
</main><footer class="site-footer"><p>Loopback only · read only · current news.search@{_CURRENT_NEWS_TOOL_VERSION}</p></footer></div></body></html>"""


def _render_page(
    result: Mapping[str, Any],
    raw_query: Mapping[str, str],
    revision: str,
    error: str | None,
) -> str:
    view = str(result["view"])
    navigation = "".join(
        '<a href="/?' + html.escape(urlencode({"view": item}), quote=True) + '"'
        + (' aria-current="page"' if item == view else "")
        + f">{html.escape(_VIEW_LABELS[item])}</a>"
        for item in _VIEWS
    )
    navigation += (
        '<a href="/news">Current news</a>'
        '<a href="/data-status">Data status</a>'
        '<a href="/agent-tools">Agent Tools</a>'
    )
    notice = (
        f'<div class="state-notice warning" role="alert">{html.escape(error)}</div>'
        if error
        else ""
    )
    form = _render_form(view, result.get("query", {}), result)
    columns = tuple(str(item) for item in result.get("columns", ()))
    rows = result.get("rows", ())
    header = "".join(f"<th>{html.escape(item.replace('_', ' ').title())}</th>" for item in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{_html_value(row.get(column))}</td>" for column in columns) + "</tr>"
        for row in rows
        if isinstance(row, Mapping)
    )
    if not body:
        body = f'<tr><td colspan="{max(1, len(columns))}">No rows match this selection.</td></tr>'
    page = int(result.get("page", 1))
    limit = int(result.get("limit", 25))
    total = int(result.get("total", 0))
    pager = _render_pager(raw_query, view, page, limit, total)
    api_query = dict(raw_query)
    api_query["view"] = view
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Canonical Data Inspector</title><link rel="stylesheet" href="/assets/dashboard.css"></head>
<body><a class="skip-link" href="#main-content">Skip to content</a><div class="app-shell">
<header class="site-header"><a class="brand" href="/"><span class="brand-mark">QD</span><strong>Canonical Data Inspector</strong></a>
<p class="shell-status"><span aria-hidden="true">●</span> Local read-only · registry {html.escape(revision)}</p></header>
<nav class="primary-nav" aria-label="Inspector views">{navigation}</nav>
<main id="main-content"><section class="page-intro"><p class="eyebrow">Operational data · inspection only</p>
<h1>{html.escape(_VIEW_LABELS[view])}</h1><p>Fixed, bounded queries over the canonical market, macro, and company databases, plus the canonical news database. Database paths, SQL, writes, and provider calls are not available here.</p></section>
{notice}<section class="panel"><div class="panel-header"><div><p class="panel-kicker">Filters</p><h2>Choose what to inspect</h2></div></div>{form}</section>
<section class="panel"><div class="panel-header"><div><p class="panel-kicker">Results</p><h2>{total:,} matching rows</h2></div>
<a href="/api/rows?{html.escape(urlencode(api_query), quote=True)}">View JSON</a></div>
<div class="table-scroll"><table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table></div>{pager}</section>
</main><footer class="site-footer"><p>Loopback only · GET only · no database mutation</p></footer></div></body></html>"""


def _render_form(view: str, query: Mapping[str, Any], result: Mapping[str, Any]) -> str:
    direction = str(result.get("direction", "desc"))
    limit = str(result.get("limit", 25))
    fields: list[str] = [f'<input type="hidden" name="view" value="{html.escape(view, quote=True)}">']
    if view == "market-prices":
        fields.extend(
            (
                _input("symbol", "Symbol", query.get("symbol", "AAPL")),
                _input("start_date", "Start date", query.get("start_date"), input_type="date"),
                _input("end_date", "End date", query.get("end_date"), input_type="date"),
            )
        )
    elif view == "market-instruments":
        fields.append(_input("search", "Symbol or name", query.get("search")))
    elif view == "spy-options":
        selected_type = str(query.get("option_type", ""))
        type_options = '<option value="">All option types</option>' + "".join(
            f'<option value="{item}"'
            + (" selected" if item == selected_type else "")
            + f">{item.title()}</option>"
            for item in ("call", "put")
        )
        selected_state = str(query.get("state", ""))
        state_options = '<option value="">All surface states</option>' + "".join(
            f'<option value="{item}"'
            + (" selected" if item == selected_state else "")
            + f">{item.title()}</option>"
            for item in ("present", "missing", "excluded")
        )
        fields.append(
            f'<label>Option type<select name="option_type">{type_options}</select></label>'
        )
        fields.append(
            f'<label>Surface state<select name="state">{state_options}</select></label>'
        )
        fields.append(
            _input(
                "expiration", "Exact expiration", query.get("expiration"),
                input_type="date",
            )
        )
    elif view == "options-surfaces":
        selected_underlying = str(query.get("underlying", ""))
        underlying_options = '<option value="">All configured underlyings</option>' + "".join(
            f'<option value="{html.escape(item, quote=True)}"'
            + (" selected" if item == selected_underlying else "")
            + f">{html.escape(item)}</option>"
            for item in _OPTIONS_SURFACE_UNDERLYINGS
        )
        fields.append(
            f'<label>Underlying<select name="underlying">{underlying_options}</select></label>'
        )
        selected_target_dte = str(query.get("target_dte", ""))
        target_dte_options = '<option value="">All target DTEs</option>' + "".join(
            f'<option value="{item}"'
            + (" selected" if str(item) == selected_target_dte else "")
            + f">{item} DTE</option>"
            for item in _OPTIONS_SURFACE_TARGET_DTES
        )
        fields.append(
            f'<label>Target DTE<select name="target_dte">{target_dte_options}</select></label>'
        )
        selected_type = str(query.get("option_type", ""))
        type_options = '<option value="">All option types</option>' + "".join(
            f'<option value="{item}"'
            + (" selected" if item == selected_type else "")
            + f">{item.title()}</option>"
            for item in ("call", "put")
        )
        fields.append(
            f'<label>Option type<select name="option_type">{type_options}</select></label>'
        )
        selected_state = str(query.get("state", ""))
        state_options = '<option value="">All surface states</option>' + "".join(
            f'<option value="{item}"'
            + (" selected" if item == selected_state else "")
            + f">{item.title()}</option>"
            for item in ("present", "missing", "excluded")
        )
        fields.append(
            f'<label>Surface state<select name="state">{state_options}</select></label>'
        )
        fields.append(
            _input(
                "expiration", "Exact expiration", query.get("expiration"),
                input_type="date",
            )
        )
    elif view == "company-fundamentals":
        fields.append(
            _input("cik", "Exact SEC CIK", query.get("cik"), placeholder="0000320193")
        )
        selected = str(query.get("metric", ""))
        options = '<option value="">All reviewed metrics</option>' + "".join(
            f'<option value="{html.escape(item, quote=True)}"'
            + (" selected" if item == selected else "")
            + f">{html.escape(item.replace('_', ' ').title())}</option>"
            for item in _COMPANY_METRICS
        )
        fields.append(f'<label>Metric<select name="metric">{options}</select></label>')
        fields.append(
            _input(
                "period_end", "Exact period end", query.get("period_end"),
                input_type="date",
            )
        )
    elif view in {"macro-current", "macro-vintages"}:
        selected = str(query.get("series", _DEFAULT_SERIES))
        options = "".join(
            f'<option value="{html.escape(item, quote=True)}"'
            + (" selected" if item == selected else "")
            + f">{html.escape(item)}</option>"
            for item in _MACRO_SERIES
        )
        fields.append(f'<label>Series<select name="series">{options}</select></label>')
        fields.append(_input("period", "Exact period", query.get("period"), placeholder="2026Q2 or 2026-07"))
    elif view == "treasury-curve":
        selected = str(query.get("tenor", ""))
        options = '<option value="">All tenors</option>' + "".join(
            f'<option value="{html.escape(item, quote=True)}"'
            + (" selected" if item == selected else "")
            + f">{html.escape(item)}</option>"
            for item in _TREASURY_TENORS
        )
        fields.append(f'<label>Tenor<select name="tenor">{options}</select></label>')
        fields.append(_input("start_date", "Start date", query.get("start_date"), input_type="date"))
        fields.append(_input("end_date", "End date", query.get("end_date"), input_type="date"))
    elif view == "overnight-rates":
        selected = str(query.get("rate", ""))
        options = '<option value="">All series</option>' + "".join(
            f'<option value="{html.escape(item.code, quote=True)}"'
            + (" selected" if item.code == selected else "")
            + f">{html.escape(item.title)}</option>"
            for item in RATE_MANIFEST
        )
        fields.append(f'<label>Series<select name="rate">{options}</select></label>')
        fields.append(_input("start_date", "Start date", query.get("start_date"), input_type="date"))
        fields.append(_input("end_date", "End date", query.get("end_date"), input_type="date"))
    elif view == "repo-facilities":
        selected = str(query.get("facility", ""))
        options = '<option value="">All facilities</option>' + "".join(
            f'<option value="{html.escape(item, quote=True)}"'
            + (" selected" if item == selected else "")
            + f">{html.escape(item)}</option>"
            for item in _REPO_FACILITY_CODES
        )
        fields.append(
            f'<label>Facility<select name="facility">{options}</select></label>'
        )
        fields.append(_input("start_date", "Start date", query.get("start_date"), input_type="date"))
        fields.append(_input("end_date", "End date", query.get("end_date"), input_type="date"))
    elif view == "soma-summary":
        selected = str(query.get("component", ""))
        options = '<option value="">All components</option>' + "".join(
            f'<option value="{html.escape(item, quote=True)}"'
            + (" selected" if item == selected else "")
            + f">{html.escape(item.replace('_', ' ').title())}</option>"
            for item in _SOMA_COMPONENTS
        )
        fields.append(
            f'<label>Component<select name="component">{options}</select></label>'
        )
        fields.append(
            _input(
                "start_date",
                "Start date",
                query.get("start_date"),
                input_type="date",
            )
        )
        fields.append(
            _input(
                "end_date",
                "End date",
                query.get("end_date"),
                input_type="date",
            )
        )
    elif view == "crude-oil-stocks":
        fields.append(
            _input(
                "start_date",
                "Start date",
                query.get("start_date"),
                input_type="date",
            )
        )
        fields.append(
            _input(
                "end_date",
                "End date",
                query.get("end_date"),
                input_type="date",
            )
        )
    elif view == "electricity-retail":
        selected = str(query.get("series", ""))
        options = '<option value="">All series</option>' + "".join(
            f'<option value="{html.escape(series_id, quote=True)}"'
            + (" selected" if series_id == selected else "")
            + f">{html.escape(title)}</option>"
            for series_id, title in _EIA_RETAIL_SERIES
        )
        fields.append(
            f'<label>Series<select name="series">{options}</select></label>'
        )
        fields.append(
            _input(
                "start_period",
                "Start month",
                query.get("start_period"),
                placeholder="2020-01",
            )
        )
        fields.append(
            _input(
                "end_period",
                "End month",
                query.get("end_period"),
                placeholder="2026-06",
            )
        )
    elif view in _OFFICIAL_VIEW_MANIFESTS:
        selected = str(query.get("series", ""))
        _, manifest = _OFFICIAL_VIEW_MANIFESTS[view]
        options = '<option value="">All series</option>' + "".join(
            f'<option value="{html.escape(item.series_id, quote=True)}"'
            + (" selected" if item.series_id == selected else "")
            + f">{html.escape(item.title)}</option>"
            for item in manifest
        )
        fields.append(
            f'<label>Series<select name="series">{options}</select></label>'
        )
        fields.append(
            _input(
                "start_date",
                "Start date",
                query.get("start_date"),
                input_type="date",
            )
        )
        fields.append(
            _input(
                "end_date",
                "End date",
                query.get("end_date"),
                input_type="date",
            )
        )
    elif view == "macro-surprises":
        selected = str(query.get("kind", GDP_ADVANCE_KIND))
        options = "".join(
            f'<option value="{html.escape(item, quote=True)}"'
            + (" selected" if item == selected else "")
            + f">{html.escape('GDP growth — best available release' if item == GDP_ADVANCE_KIND else item)}</option>"
            for item in ALL_SURPRISE_KINDS
        )
        fields.append(f'<label>Release type<select name="kind">{options}</select></label>')
        stage = str(query.get("stage", ""))
        stage_options = '<option value="">All selected stages</option>' + "".join(
            f'<option value="{html.escape(item, quote=True)}"'
            + (" selected" if item == stage else "")
            + f">{html.escape(item.title())}</option>"
            for item in _GDP_RELEASE_STAGES
        )
        fields.append(f'<label>GDP release stage<select name="stage">{stage_options}</select></label>')
        fields.append(_input("start_date", "Start date", query.get("start_date"), input_type="date"))
        fields.append(_input("end_date", "End date", query.get("end_date"), input_type="date"))
    else:
        selected_mode = str(query.get("mode", "current"))
        mode_options = "".join(
            f'<option value="{item}"'
            + (" selected" if item == selected_mode else "")
            + f">{label}</option>"
            for item, label in (("current", "Current"), ("history", "History"))
        )
        fields.append(
            f'<label>View<select name="mode">{mode_options}</select></label>'
        )
        selected = str(query.get("priority", ""))
        options = '<option value="">All priorities</option>' + "".join(
            f'<option value="{html.escape(item, quote=True)}"'
            + (" selected" if item == selected else "")
            + f">{html.escape(item)}</option>"
            for item in _FMP_PRIORITIES
        )
        fields.append(_input("country", "Country", query.get("country"), placeholder="US"))
        fields.append(f'<label>Priority / impact<select name="priority">{options}</select></label>')
        fields.append(
            _input(
                "event_name", "Event name contains", query.get("event_name"),
                placeholder="Non Farm Payrolls",
            )
        )
        fields.append(
            _input(
                "keyword", "Keyword in raw row", query.get("keyword"),
                placeholder="inflation, housing, jobs",
            )
        )
        fields.append(_input("start_date", "Start date", query.get("start_date"), input_type="date"))
        fields.append(_input("end_date", "End date", query.get("end_date"), input_type="date"))
    fields.append(
        '<label>Order<select name="direction">'
        f'<option value="desc"{" selected" if direction == "desc" else ""}>Newest first</option>'
        f'<option value="asc"{" selected" if direction == "asc" else ""}>Oldest first</option>'
        "</select></label>"
    )
    fields.append(
        '<label>Rows<select name="limit">'
        + "".join(
            f'<option value="{item}"{" selected" if limit == str(item) else ""}>{item}</option>'
            for item in (10, 25, 50, 100)
        )
        + "</select></label>"
    )
    return '<form class="query-form" method="get" action="/"><fieldset><legend>Fixed query</legend><div class="form-grid">' + "".join(fields) + '</div><div class="form-actions"><button type="submit">Inspect</button></div></fieldset></form>'


def _input(
    name: str,
    label: str,
    value: Any,
    *,
    input_type: str = "text",
    placeholder: str = "",
) -> str:
    rendered = "" if value is None else str(value)
    return (
        f'<label>{html.escape(label)}<input type="{input_type}" name="{html.escape(name, quote=True)}" '
        f'value="{html.escape(rendered, quote=True)}" placeholder="{html.escape(placeholder, quote=True)}"></label>'
    )


def _render_pager(
    raw_query: Mapping[str, str], view: str, page: int, limit: int, total: int
) -> str:
    links: list[str] = []
    if page > 1:
        previous = dict(raw_query)
        previous.update({"view": view, "page": str(page - 1), "limit": str(limit)})
        links.append(f'<a href="/?{html.escape(urlencode(previous), quote=True)}">Previous</a>')
    if page * limit < total:
        following = dict(raw_query)
        following.update({"view": view, "page": str(page + 1), "limit": str(limit)})
        links.append(f'<a href="/?{html.escape(urlencode(following), quote=True)}">Next</a>')
    return '<div class="form-actions">' + " · ".join(links) + "</div>" if links else ""


def _html_value(value: Any) -> str:
    if value is None:
        return '<span class="null">—</span>'
    return html.escape(str(value), quote=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect canonical market, macro, and company data locally"
    )
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--port", type=int, default=8765)
    arguments = parser.parse_args(argv)
    try:
        application = build_canonical_inspector(arguments.project_root)
        server = create_server(
            application,
            host="127.0.0.1",
            port=arguments.port,
        )
    except QuantDataError as exc:
        print(f"Canonical Data Inspector unavailable: {exc.safe_message}", file=sys.stderr)
        return 69
    except OSError:
        print(
            "Canonical Data Inspector unavailable: the requested loopback "
            "port cannot be opened",
            file=sys.stderr,
        )
        return 69
    print(f"Canonical Data Inspector: http://127.0.0.1:{server.server_port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "CanonicalInspectorApplication",
    "CanonicalInspectorReadService",
    "build_canonical_inspector",
    "main",
)
