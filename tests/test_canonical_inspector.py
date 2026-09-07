from __future__ import annotations

import copy
import hashlib
import http.client
import io
import re
import sqlite3
import tempfile
import threading
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch

from quant_data.boundary import create_server
from quant_data.canonical_inspector import (
    CanonicalInspectorApplication,
    build_canonical_inspector,
    main as inspector_main,
)
from quant_data.errors import StoreUnavailableError, ValidationError
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.macro.fmp_calendar_wholesale import parse_fmp_us_calendar_wholesale
from quant_data.macro.fmp_release_surprises import (
    CPI_HEADLINE_MOM_KIND,
    NONFARM_PAYROLLS_KIND,
    UNEMPLOYMENT_RATE_KIND,
    ReleaseSurprise,
)
from quant_data.migrations import initialize_all
from quant_data.registry import CANONICAL_REGISTRY_PATH, load_registry
from quant_data.stores import StoreMap, StoreRole, stable_id


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CanonicalInspectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        root = Path(self.temporary.name)
        self.market = root / "market.sqlite"
        self.macro = root / "macro.sqlite"
        self.company = root / "company.sqlite"
        self._seed_market(self.market)
        self._seed_spy_options(self.market)
        self._seed_macro(self.macro)
        self._seed_company(self.company)
        self.registry = load_registry(
            CANONICAL_REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        self.stores = StoreMap.four_explicit(
            market=self.market,
            macro=self.macro,
            company=self.company,
            news=root / "news.sqlite",
        )
        self.application = CanonicalInspectorApplication(self.stores, self.registry)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _seed_market(path: Path) -> None:
        connection = sqlite3.connect(path)
        connection.executescript(
            """
            CREATE TABLE store_metadata (singleton INTEGER PRIMARY KEY, store_role TEXT NOT NULL);
            INSERT INTO store_metadata VALUES (1, 'market');
            CREATE TABLE stage10_instruments (
                instrument_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                provider_symbol TEXT NOT NULL,
                asset_type TEXT NOT NULL,
                display_name TEXT,
                exchange_code TEXT,
                first_trade_date TEXT
            );
            CREATE TABLE stage10_daily_price_versions (
                version_id TEXT PRIMARY KEY,
                instrument_id TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                open_value TEXT NOT NULL,
                high_value TEXT NOT NULL,
                low_value TEXT NOT NULL,
                close_value TEXT NOT NULL,
                volume INTEGER NOT NULL,
                available_at TEXT NOT NULL,
                captured_at TEXT NOT NULL
            );
            CREATE TABLE stage10_daily_prices (
                instrument_id TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                current_version_id TEXT NOT NULL
            );
            INSERT INTO stage10_instruments VALUES
              ('i-aapl', 'fmp', 'AAPL', 'equity', 'Apple Inc.', 'NASDAQ', '1980-12-12'),
              ('i-spy', 'fmp', 'SPY', 'etf', 'SPDR S&P 500 ETF Trust', 'NYSE', '1993-01-29'),
              ('i-qqq', 'fmp', 'QQQ', 'etf', 'Invesco QQQ Trust', 'NASDAQ', '1999-03-10');
            INSERT INTO stage10_daily_price_versions VALUES
              ('v-aapl-1', 'i-aapl', '2026-08-14', '220.1', '224.0', '219.5', '223.7', 1000,
               '2026-08-15T00:00:00Z', '2026-08-15T00:00:00Z'),
              ('v-spy-1', 'i-spy', '2026-08-14', '640.0', '644.0', '639.0', '643.0', 2000,
               '2026-08-15T00:00:00Z', '2026-08-15T00:00:00Z');
            INSERT INTO stage10_daily_prices VALUES
              ('i-aapl', '2026-08-14', 'v-aapl-1'),
              ('i-spy', '2026-08-14', 'v-spy-1');
            """
        )
        connection.commit()
        connection.close()
        Path(str(path) + chr(45) + chr(119) + chr(97) + chr(108)).touch()

    @staticmethod
    def _seed_spy_options(path: Path) -> None:
        connection = sqlite3.connect(path)
        connection.executescript(
            """
            CREATE TABLE option_surface_captures (
                capture_id TEXT PRIMARY KEY,
                underlying_instrument_id TEXT NOT NULL,
                requested_feed TEXT NOT NULL,
                resolved_feed TEXT NOT NULL,
                environment TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                available_at TEXT NOT NULL,
                completeness TEXT NOT NULL,
                request_scope_json TEXT NOT NULL
            );
            CREATE TABLE option_contracts (
                contract_id TEXT PRIMARY KEY,
                underlying_instrument_id TEXT NOT NULL,
                contract_symbol TEXT NOT NULL,
                expiration_date TEXT NOT NULL,
                strike_price TEXT NOT NULL,
                option_type TEXT NOT NULL,
                deliverable_kind TEXT NOT NULL,
                contract_multiplier INTEGER NOT NULL,
                contract_status TEXT NOT NULL
            );
            CREATE TABLE option_surface_snapshots (
                surface_snapshot_id TEXT PRIMARY KEY,
                capture_id TEXT NOT NULL,
                contract_id TEXT NOT NULL,
                surface_state TEXT NOT NULL,
                missing_reason TEXT,
                exclusion_reason TEXT,
                bid_price REAL,
                ask_price REAL,
                last_price REAL,
                implied_volatility REAL,
                delta REAL,
                gamma REAL,
                theta REAL,
                vega REAL,
                rho REAL,
                quote_at TEXT,
                trade_at TEXT
            );
            CREATE TABLE option_open_interest (
                open_interest_id TEXT PRIMARY KEY,
                capture_id TEXT NOT NULL,
                contract_id TEXT NOT NULL,
                as_of_date TEXT NOT NULL,
                open_interest INTEGER,
                observation_state TEXT NOT NULL,
                missing_reason TEXT,
                source_row INTEGER NOT NULL
            );
            CREATE TABLE option_close_prices (
                close_price_id TEXT PRIMARY KEY,
                capture_id TEXT NOT NULL,
                contract_id TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                close_price REAL,
                observation_state TEXT NOT NULL,
                missing_reason TEXT,
                source_row INTEGER NOT NULL
            );
            CREATE TABLE option_capture_underlying_quotes (
                underlying_quote_id TEXT PRIMARY KEY,
                capture_id TEXT NOT NULL,
                input_state TEXT NOT NULL,
                missing_reason TEXT,
                bid_price REAL,
                ask_price REAL,
                last_price REAL,
                trade_price REAL,
                quote_at TEXT
            );
            CREATE TABLE option_capture_rate_curves (
                rate_curve_id TEXT PRIMARY KEY,
                capture_id TEXT NOT NULL,
                curve_date TEXT,
                source_name TEXT,
                input_state TEXT NOT NULL,
                missing_reason TEXT
            );
            CREATE TABLE option_capture_dividend_sets (
                dividend_set_id TEXT PRIMARY KEY,
                capture_id TEXT NOT NULL,
                source_name TEXT,
                input_state TEXT NOT NULL,
                missing_reason TEXT
            );
            CREATE TABLE option_capture_expiry_inputs (
                expiry_input_id TEXT PRIMARY KEY,
                capture_id TEXT NOT NULL,
                expiration_date TEXT NOT NULL,
                input_state TEXT NOT NULL,
                missing_reason TEXT,
                spot_price REAL,
                risk_free_rate REAL,
                dividend_yield REAL,
                forward_price REAL
            );
            INSERT INTO option_surface_captures VALUES
              ('capture-old', 'i-spy', 'indicative', 'alpaca_indicative', 'paper',
               '2026-08-22T19:55:00Z', '2026-08-22T19:55:00Z', 'complete',
               '{"underlying_symbol":"SPY","target_dte":30,"selected_expiration":"2026-09-19","spot_price":"640"}'),
              ('capture-current', 'i-spy', 'indicative', 'alpaca_indicative', 'paper',
               '2026-08-25T19:55:00Z', '2026-08-25T19:55:00Z', 'complete',
               '{"underlying_symbol":"SPY","target_dte":30,"selected_expiration":"2026-09-25","spot_price":"645"}'),
              ('capture-qqq-old', 'i-qqq', 'indicative', 'alpaca_indicative', 'paper',
               '2026-08-24T19:55:00Z', '2026-08-24T19:55:00Z', 'complete',
               '{"underlying_symbol":"QQQ","target_dtes":[7,14],"selected_expiration":"2026-09-26","spot_price":"574"}'),
              ('capture-qqq-current', 'i-qqq', 'indicative', 'alpaca_indicative', 'paper',
               '2026-08-25T19:56:00Z', '2026-08-25T19:56:00Z', 'complete',
               '{"underlying_symbol":"QQQ","target_dtes":[7,14],"selected_expiration":"2026-09-26","spot_price":"575"}');
            INSERT INTO option_contracts VALUES
              ('contract-old', 'i-spy', 'SPY260919C00640000', '2026-09-19', '640',
               'call', 'standard', 100, 'active'),
              ('contract-call', 'i-spy', 'SPY260925C00650000', '2026-09-25', '650',
               'call', 'standard', 100, 'active'),
              ('contract-put', 'i-spy', 'SPY260925P00640000', '2026-09-25', '640',
               'put', 'standard', 100, 'active'),
              ('contract-missing', 'i-spy', 'SPY260925C00655000', '2026-09-25', '655',
               'call', 'standard', 100, 'active'),
              ('contract-qqq-call', 'i-qqq', 'QQQ260926C00575000', '2026-09-26', '575',
               'call', 'standard', 100, 'active'),
              ('contract-qqq-put', 'i-qqq', 'QQQ260926P00570000', '2026-09-26', '570',
               'put', 'standard', 100, 'active');
            INSERT INTO option_surface_snapshots VALUES
              ('surface-old', 'capture-old', 'contract-old', 'present', NULL, NULL,
               5.0, 5.1, 5.05, 0.20, 0.50, 0.01, -0.04, 0.10, 0.02,
               '2026-08-22T19:54:00Z', '2026-08-22T19:54:00Z'),
              ('surface-call', 'capture-current', 'contract-call', 'present', NULL, NULL,
               4.1, 4.2, 4.15, 0.205, 0.51, 0.012, -0.045, 0.11, 0.02,
               '2026-08-25T19:54:00.123456789Z', '2026-08-25T19:54:00.123456788Z'),
              ('surface-put', 'capture-current', 'contract-put', 'excluded', NULL,
               'provider_excluded', NULL, NULL, NULL, NULL, NULL, NULL, NULL,
               NULL, NULL, NULL, NULL),
              ('surface-missing', 'capture-current', 'contract-missing', 'missing',
               'quote_unavailable', NULL, NULL, NULL, NULL, NULL, NULL, NULL,
               NULL, NULL, NULL, NULL, NULL),
              ('surface-qqq-old', 'capture-qqq-old', 'contract-qqq-call', 'present', NULL, NULL,
               8.1, 8.2, 8.15, 0.22, 0.54, 0.013, -0.05, 0.12, 0.021,
               '2026-08-24T19:54:00Z', '2026-08-24T19:54:00Z'),
              ('surface-qqq-call', 'capture-qqq-current', 'contract-qqq-call', 'present', NULL, NULL,
               8.3, 8.4, 8.35, 0.221, 0.55, 0.014, -0.051, 0.13, 0.022,
               '2026-08-25T19:55:00Z', '2026-08-25T19:55:00Z'),
              ('surface-qqq-put', 'capture-qqq-current', 'contract-qqq-put', 'missing',
               'quote_unavailable', NULL, NULL, NULL, NULL, NULL, NULL, NULL,
               NULL, NULL, NULL, NULL, NULL);
            INSERT INTO option_open_interest VALUES
              ('oi-call', 'capture-current', 'contract-call', '2026-08-25', 12345,
               'present', NULL, 1),
              ('oi-qqq-call', 'capture-qqq-current', 'contract-qqq-call', '2026-08-25', 54321,
               'present', NULL, 1),
              ('oi-qqq-put', 'capture-qqq-current', 'contract-qqq-put', '2026-08-25', NULL,
               'missing', 'source_not_provided', 1);
            INSERT INTO option_close_prices VALUES
              ('close-call', 'capture-current', 'contract-call', '2026-08-25', 4.05,
               'present', NULL, 1),
              ('close-qqq-call', 'capture-qqq-current', 'contract-qqq-call', '2026-08-25', 8.25,
               'present', NULL, 1),
              ('close-qqq-put', 'capture-qqq-current', 'contract-qqq-put', '2026-08-25', NULL,
               'missing', 'source_not_provided', 1);
            INSERT INTO option_capture_underlying_quotes VALUES
              ('underlying-current', 'capture-current', 'present', NULL, 644.9, 645.1,
               645.0, 645.0, '2026-08-25T19:54:00.123456700Z'),
              ('underlying-qqq-current', 'capture-qqq-current', 'present', NULL, 574.9, 575.1,
               NULL, NULL, '2026-08-25T19:55:00Z');
            INSERT INTO option_capture_rate_curves VALUES
              ('rate-old', 'capture-old', NULL, NULL, 'missing',
               'not_collected_in_live_v1'),
              ('rate-current', 'capture-current', NULL, NULL, 'missing',
               'not_collected_in_live_v1'),
              ('rate-qqq-old', 'capture-qqq-old', NULL, NULL, 'missing',
               'not_collected_in_live_etf_grid'),
              ('rate-qqq-current', 'capture-qqq-current', NULL, NULL, 'missing',
               'not_collected_in_live_etf_grid');
            INSERT INTO option_capture_dividend_sets VALUES
              ('dividend-old', 'capture-old', NULL, 'missing',
               'not_collected_in_live_v1'),
              ('dividend-current', 'capture-current', NULL, 'missing',
               'not_collected_in_live_v1'),
              ('dividend-qqq-old', 'capture-qqq-old', NULL, 'missing',
               'not_collected_in_live_etf_grid'),
              ('dividend-qqq-current', 'capture-qqq-current', NULL, 'missing',
               'not_collected_in_live_etf_grid');
            INSERT INTO option_capture_expiry_inputs VALUES
              ('expiry-old', 'capture-old', '2026-09-19', 'missing',
               'synchronized_model_inputs_not_collected_in_live_v1', NULL, NULL, NULL, NULL),
              ('expiry-current', 'capture-current', '2026-09-25', 'missing',
               'synchronized_model_inputs_not_collected_in_live_v1', NULL, NULL, NULL, NULL),
              ('expiry-qqq-old', 'capture-qqq-old', '2026-09-26', 'missing',
               'synchronized_model_inputs_not_collected_in_live_v1', NULL, NULL, NULL, NULL),
              ('expiry-qqq-current', 'capture-qqq-current', '2026-09-26', 'missing',
               'synchronized_model_inputs_not_collected_in_live_etf_grid',
               NULL, NULL, NULL, NULL);
            """
        )
        connection.commit()
        connection.close()

    @staticmethod
    def _seed_company(path: Path) -> None:
        revenue_metric_id = stable_id("company_metric", "revenue")
        assets_metric_id = stable_id("company_metric", "total_assets")
        connection = sqlite3.connect(path)
        connection.executescript(
            f"""
            CREATE TABLE store_metadata (
                singleton INTEGER PRIMARY KEY,
                store_role TEXT NOT NULL
            );
            INSERT INTO store_metadata VALUES (1, 'company');
            CREATE TABLE company_issuers (
                issuer_id TEXT PRIMARY KEY,
                cik TEXT NOT NULL
            );
            CREATE TABLE company_issuer_versions (
                issuer_version_id TEXT PRIMARY KEY,
                issuer_id TEXT NOT NULL,
                legal_name TEXT,
                name_state TEXT NOT NULL,
                version_sequence INTEGER NOT NULL
            );
            CREATE TABLE company_metric_definitions (
                metric_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL
            );
            CREATE TABLE company_metric_mappings (
                mapping_id TEXT PRIMARY KEY,
                metric_id TEXT NOT NULL,
                mapping_version TEXT NOT NULL,
                taxonomy TEXT NOT NULL,
                concept TEXT NOT NULL,
                unit TEXT NOT NULL
            );
            CREATE TABLE company_sec_filings (
                accession_number TEXT PRIMARY KEY,
                form_type TEXT NOT NULL,
                filing_date TEXT NOT NULL
            );
            CREATE TABLE company_fundamental_observation_versions (
                fundamental_version_id TEXT PRIMARY KEY,
                issuer_id TEXT NOT NULL,
                metric_id TEXT NOT NULL,
                mapping_id TEXT NOT NULL,
                share_semantics TEXT NOT NULL,
                fiscal_year INTEGER,
                fiscal_period TEXT,
                reference_period_start TEXT,
                reference_period_end TEXT NOT NULL,
                accession_number TEXT NOT NULL,
                value_text TEXT,
                value_state TEXT NOT NULL,
                missing_reason TEXT,
                available_at TEXT NOT NULL,
                available_precision TEXT NOT NULL,
                version_sequence INTEGER NOT NULL
            );
            INSERT INTO company_issuers VALUES
              ('issuer-aapl', '0000320193'),
              ('issuer-msft', '0000789019');
            INSERT INTO company_issuer_versions VALUES
              ('issuer-aapl-v1', 'issuer-aapl', 'Apple Inc.', 'present', 1),
              ('issuer-aapl-v2', 'issuer-aapl', 'Apple Inc.', 'present', 2),
              ('issuer-msft-v1', 'issuer-msft', 'Microsoft Corporation', 'present', 1);
            INSERT INTO company_metric_definitions VALUES
              ('{revenue_metric_id}', 'Revenue'),
              ('{assets_metric_id}', 'Total assets');
            INSERT INTO company_metric_mappings VALUES
              (
                'mapping-revenue', '{revenue_metric_id}', 'sec-core-v2', 'us-gaap',
                'RevenueFromContractWithCustomerExcludingAssessedTax', 'USD'
              ),
              (
                'mapping-assets', '{assets_metric_id}', 'sec-core-v2', 'us-gaap',
                'Assets', 'USD'
              );
            INSERT INTO company_sec_filings VALUES
              ('0000320193-24-000123', '10-K', '2024-11-01'),
              ('0000789019-23-000123', '10-K', '2023-07-27');
            INSERT INTO company_fundamental_observation_versions VALUES
              (
                'fundamental-revenue-v1', 'issuer-aapl', '{revenue_metric_id}',
                'mapping-revenue', 'not_share', 2024, 'FY', '2023-10-01',
                '2024-09-28', '0000320193-24-000123', '380000000000',
                'present', NULL, '2024-11-01T14:00:00Z', 'datetime', 1
              ),
              (
                'fundamental-revenue-v2', 'issuer-aapl', '{revenue_metric_id}',
                'mapping-revenue', 'not_share', 2024, 'FY', '2023-10-01',
                '2024-09-28', '0000320193-24-000123', '383285000000',
                'present', NULL, '2024-11-01T14:00:00Z', 'datetime', 2
              ),
              (
                'fundamental-assets-v1', 'issuer-aapl', '{assets_metric_id}',
                'mapping-assets', 'not_share', 2024, 'FY', NULL,
                '2024-09-28', '0000320193-24-000123', '364980000000',
                'present', NULL, '2024-11-01T14:00:00Z', 'datetime', 1
              ),
              (
                'fundamental-msft-revenue-v1', 'issuer-msft', '{revenue_metric_id}',
                'mapping-revenue', 'not_share', 2023, 'FY', '2022-07-01',
                '2023-06-30', '0000789019-23-000123', '211915000000',
                'present', NULL, '2023-07-27T20:00:00Z', 'datetime', 1
              );
            """
        )
        connection.commit()
        connection.close()

    @staticmethod
    def _seed_macro(path: Path) -> None:
        connection = sqlite3.connect(path)
        connection.executescript(
            """
            CREATE TABLE store_metadata (singleton INTEGER PRIMARY KEY, store_role TEXT NOT NULL);
            INSERT INTO store_metadata VALUES (1, 'macro');
            CREATE TABLE macro_live_vintage_series (
                series_id TEXT PRIMARY KEY,
                title TEXT NOT NULL
            );
            CREATE TABLE macro_live_vintage_releases (
                release_id TEXT PRIMARY KEY,
                source_vintage_identity TEXT NOT NULL,
                vintage_at TEXT,
                release_stage TEXT,
                is_first_release INTEGER,
                source_release_order TEXT NOT NULL
            );
            CREATE TABLE macro_live_vintage_observation_versions (
                version_id TEXT PRIMARY KEY,
                series_id TEXT NOT NULL,
                period TEXT NOT NULL,
                release_id TEXT NOT NULL,
                value_text TEXT NOT NULL,
                unit TEXT NOT NULL,
                correction_sequence INTEGER NOT NULL,
                available_at TEXT NOT NULL,
                captured_at TEXT NOT NULL
            );
            CREATE TABLE macro_live_vintage_observations (
                series_id TEXT NOT NULL,
                period TEXT NOT NULL,
                current_version_id TEXT NOT NULL
            );
            INSERT INTO macro_live_vintage_series VALUES
              ('macro.gdp.real_qoq_saar_pct', 'Real GDP growth');
            INSERT INTO macro_live_vintage_releases VALUES
              ('r-2026q2', '2026-07-30-advance', '2026-07-30', 'advance', 1, '2026-07-30');
            INSERT INTO macro_live_vintage_observation_versions VALUES
              ('gdp-v1', 'macro.gdp.real_qoq_saar_pct', '2026Q2', 'r-2026q2', '3.0', 'percent', 1,
               '2026-07-30', '2026-08-17T12:00:00Z');
            INSERT INTO macro_live_vintage_observations VALUES
              ('macro.gdp.real_qoq_saar_pct', '2026Q2', 'gdp-v1');

            CREATE TABLE macro_series (
                series_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                provider_series_code TEXT NOT NULL,
                title TEXT NOT NULL
            );
            CREATE TABLE macro_observation_versions (
                version_id TEXT PRIMARY KEY,
                series_id TEXT NOT NULL,
                period_start TEXT NOT NULL,
                value_text TEXT,
                missing_reason TEXT,
                correction_sequence INTEGER NOT NULL,
                available_at TEXT NOT NULL,
                captured_at TEXT NOT NULL
            );
            CREATE TABLE macro_observations (
                series_id TEXT NOT NULL,
                period_start TEXT NOT NULL,
                current_version_id TEXT NOT NULL
            );
            INSERT INTO macro_series VALUES
              ('macro.nyfed.effr', 'nyfed', 'EFFR', 'Effective Federal Funds Rate'),
              ('macro.nyfed.sofr', 'nyfed', 'SOFR', 'Secured Overnight Financing Rate'),
              ('macro.nyfed.on_rrp_accepted_amount', 'nyfed', 'ON_RRP', 'Overnight Reverse Repo Facility Accepted Amount'),
              ('macro.nyfed.srf_accepted_amount', 'nyfed', 'SRF', 'Standing Repo Facility Accepted Amount');
            INSERT INTO macro_observation_versions VALUES
              (
                'effr-20260820-v1', 'macro.nyfed.effr', '2026-08-20',
                '3.62', NULL, 1, '2026-08-20T13:00:00Z', '2026-08-20T13:00:00Z'
              ),
              (
                'effr-20260820-v2', 'macro.nyfed.effr', '2026-08-20',
                '3.63', NULL, 2, '2026-08-21T13:00:00Z', '2026-08-21T13:00:00Z'
              ),
              (
                'sofr-20260820-v1', 'macro.nyfed.sofr', '2026-08-20',
                '3.64', NULL, 1, '2026-08-20T13:00:00Z', '2026-08-20T13:00:00Z'
              ),
              (
                'on-rrp-20260820-v1', 'macro.nyfed.on_rrp_accepted_amount', '2026-08-20',
                '25000000000', NULL, 1, '2026-08-21T12:00:00Z', '2026-08-21T12:00:00Z'
              ),
              (
                'srf-20260820-v1', 'macro.nyfed.srf_accepted_amount', '2026-08-20',
                '5000000', NULL, 1, '2026-08-21T12:00:00Z', '2026-08-21T12:00:00Z'
              );
            INSERT INTO macro_observations VALUES
              ('macro.nyfed.effr', '2026-08-20', 'effr-20260820-v2'),
              ('macro.nyfed.sofr', '2026-08-20', 'sofr-20260820-v1'),
              ('macro.nyfed.on_rrp_accepted_amount', '2026-08-20', 'on-rrp-20260820-v1'),
              ('macro.nyfed.srf_accepted_amount', '2026-08-20', 'srf-20260820-v1');

            ALTER TABLE macro_observation_versions
              ADD COLUMN period_end TEXT;
            ALTER TABLE macro_observation_versions
              ADD COLUMN unit TEXT;
            UPDATE macro_observation_versions
              SET period_end=period_start, unit='fixture';

            INSERT INTO macro_series VALUES
              (
                'macro.federal_reserve_h41.total_assets_less_eliminations_wednesday',
                'federal_reserve_h41', 'H41/H41/RESPPMA_N.WW',
                'Federal Reserve total assets'
              ),
              (
                'macro.federal_reserve_policy.iorb',
                'federal_reserve_policy', 'IORB',
                'Interest rate on reserve balances'
              ),
              (
                'macro.chicagofed.nfci', 'chicagofed', 'NFCI',
                'Chicago Fed National Financial Conditions Index'
              ),
              (
                'macro.chicagofed.cfnai', 'chicagofed', 'CFNAI',
                'Chicago Fed National Activity Index'
              ),
              (
                'macro.federal_reserve.industrial_production_total_sa',
                'federal_reserve_industrial_production', 'INDPRO',
                'Industrial Production: Total Index, seasonally adjusted'
              ),
              (
                'macro.bis.us_private_nonfinancial_credit_gap', 'bis',
                'WS_CREDIT_GAP/Q.US.P.A.C',
                'U.S. private non-financial sector credit-to-GDP gap'
              ),
              (
                'macro.nyfed.cmdi.market', 'nyfed_cmdi', 'Market CMDI',
                'NY Fed Corporate Bond Market Distress Index — overall market'
              ),
              (
                'macro.treasury_fiscal.daily.tga_closing_balance',
                'treasury_fiscal_data',
                'Treasury General Account (TGA) Closing Balance',
                'U.S. Treasury General Account closing balance'
              ),
              (
                'macro.treasury_fiscal.daily.total_public_debt_outstanding',
                'treasury_fiscal_data', 'tot_pub_debt_out_amt',
                'Total public debt outstanding'
              ),
              (
                'macro.treasury_fiscal.monthly.receipts',
                'treasury_fiscal_data', 'Receipts',
                'Federal receipts'
              ),
              (
                'macro.eia.weekly.lower_48_working_natural_gas_storage',
                'eia', 'NG.NW2_EPG0_SWO_R48_BCF.W',
                'Lower 48 working natural gas in underground storage'
              ),
              (
                'macro.eia.petroleum.weekly.us_total_motor_gasoline_ending_stocks',
                'eia', 'WGTSTUS1',
                'U.S. ending stocks of total motor gasoline'
              ),
              (
                'macro.eia.petroleum.weekly.us_distillate_fuel_oil_ending_stocks',
                'eia', 'WDISTUS1',
                'U.S. ending stocks of distillate fuel oil'
              ),
              (
                'macro.eia.petroleum.weekly.us_finished_motor_gasoline_product_supplied',
                'eia', 'WGFUPUS2',
                'U.S. product supplied of finished motor gasoline'
              ),
              (
                'macro.nber.us_recession_indicator', 'nber',
                'business_cycle_dates', 'U.S. recession indicator'
              ),
              (
                'macro.bls.ppi_final_demand_sa', 'bls', 'WPSFD4',
                'Producer Price Index - Final Demand, seasonally adjusted'
              ),
              (
                'macro.bls.employment_cost_index_total_compensation_civilian_sa',
                'bls', 'CIS1010000000000I',
                'Employment Cost Index - total compensation, civilian workers, seasonally adjusted'
              ),
              (
                'macro.bea.personal_income', 'bea', 'A065RC',
                'Personal income'
              ),
              (
                'macro.bea.disposable_personal_income', 'bea', 'A067RC',
                'Disposable personal income'
              ),
              (
                'macro.bea.personal_consumption_expenditures', 'bea',
                'DPCERC', 'Personal consumption expenditures'
              );
            INSERT INTO macro_observation_versions (
                version_id, series_id, period_start, value_text,
                missing_reason, correction_sequence, available_at,
                captured_at, period_end, unit
            ) VALUES
              (
                'h41-total-20260819-v1',
                'macro.federal_reserve_h41.total_assets_less_eliminations_wednesday',
                '2026-08-19', '6600001', NULL, 1,
                '2026-08-20T20:30:00Z', '2026-08-22T12:00:00Z',
                '2026-08-19', 'usd_millions'
              ),
              (
                'iorb-20260820-v1',
                'macro.federal_reserve_policy.iorb',
                '2026-08-20', '3.65', NULL, 1,
                '2026-08-20T20:30:00Z', '2026-08-22T12:00:00Z',
                '2026-08-20', 'percent'
              ),
              (
                'nfci-20260821-v1', 'macro.chicagofed.nfci',
                '2026-08-21', '-0.28', NULL, 1,
                '2026-08-22T12:00:00Z', '2026-08-22T12:00:00Z',
                '2026-08-21', 'index'
              ),
              (
                'cfnai-202607-v1', 'macro.chicagofed.cfnai',
                '2026-07-01', '0.14', NULL, 1,
                '2026-08-24T12:30:00Z', '2026-08-24T12:30:00Z',
                '2026-07-31', 'index'
              ),
              (
                'indpro-202607-v1',
                'macro.federal_reserve.industrial_production_total_sa',
                '2026-07-01', '102.9939', NULL, 1,
                '2026-08-18T13:20:00Z', '2026-08-24T12:30:00Z',
                '2026-07-31', 'index_2017_100'
              ),
              (
                'bis-gap-2026q2-v1',
                'macro.bis.us_private_nonfinancial_credit_gap',
                '2026-04-01', '-2.7', NULL, 1,
                '2026-08-22T12:00:00Z', '2026-08-22T12:00:00Z',
                '2026-06-30', 'percentage_points_of_gdp'
              ),
              (
                'cmdi-market-20260724-v1', 'macro.nyfed.cmdi.market',
                '2026-07-24', '0.15', NULL, 1,
                '2026-08-22T12:00:00Z', '2026-08-22T12:00:00Z',
                '2026-07-24', 'index'
              ),
              (
                'tga-20260820-v1',
                'macro.treasury_fiscal.daily.tga_closing_balance',
                '2026-08-20', '765432', NULL, 1,
                '2026-08-22T12:00:00Z', '2026-08-22T12:00:00Z',
                '2026-08-20', 'usd_millions'
              ),
              (
                'debt-total-20260820-v1',
                'macro.treasury_fiscal.daily.total_public_debt_outstanding',
                '2026-08-20', '37000123456789.01', NULL, 1,
                '2026-08-22T12:00:00Z', '2026-08-22T12:00:00Z',
                '2026-08-20', 'usd'
              ),
              (
                'fiscal-receipts-202607-v1',
                'macro.treasury_fiscal.monthly.receipts',
                '2026-07-01', '334010', NULL, 1,
                '2026-08-12T18:00:00Z', '2026-08-23T12:00:00Z',
                '2026-07-31', 'usd_millions'
              ),
              (
                'eia-gas-20260814-v1',
                'macro.eia.weekly.lower_48_working_natural_gas_storage',
                '2026-08-14', '3199', NULL, 1,
                '2026-08-22T12:00:00Z', '2026-08-22T12:00:00Z',
                '2026-08-14', 'bcf'
              ),
              (
                'eia-gasoline-stocks-20260814-v1',
                'macro.eia.petroleum.weekly.us_total_motor_gasoline_ending_stocks',
                '2026-08-14', '235000', NULL, 1,
                '2026-08-22T12:00:00Z', '2026-08-22T12:00:00Z',
                '2026-08-14', 'thousand_barrels'
              ),
              (
                'eia-distillate-stocks-20260814-v1',
                'macro.eia.petroleum.weekly.us_distillate_fuel_oil_ending_stocks',
                '2026-08-14', '120000', NULL, 1,
                '2026-08-22T12:00:00Z', '2026-08-22T12:00:00Z',
                '2026-08-14', 'thousand_barrels'
              ),
              (
                'eia-gasoline-supplied-20260814-v1',
                'macro.eia.petroleum.weekly.us_finished_motor_gasoline_product_supplied',
                '2026-08-14', '9100', NULL, 1,
                '2026-08-22T12:00:00Z', '2026-08-22T12:00:00Z',
                '2026-08-14', 'thousand_barrels_per_day'
              ),
              (
                'nber-recession-202007-v1',
                'macro.nber.us_recession_indicator',
                '2020-07-01', '1', NULL, 1,
                '2026-08-22T12:00:00Z', '2026-08-22T12:00:00Z',
                '2020-07-31', 'indicator'
              ),
              (
                'bls-ppi-202607-v1', 'macro.bls.ppi_final_demand_sa',
                '2026-07-01', '150.1', NULL, 1,
                '2026-08-22T12:00:00Z', '2026-08-22T12:00:00Z',
                '2026-07-31', 'index_2009_11_100'
              ),
              (
                'bls-eci-2026q2-v1',
                'macro.bls.employment_cost_index_total_compensation_civilian_sa',
                '2026-04-01', '168.1', NULL, 1,
                '2026-08-22T12:00:00Z', '2026-08-22T12:00:00Z',
                '2026-06-30', 'index_2005_12_100'
              ),
              (
                'bea-pi-202606-v1', 'macro.bea.personal_income',
                '2026-06-01', '26500.1', NULL, 1,
                '2026-08-23T12:00:00Z', '2026-08-23T12:00:00Z',
                '2026-06-30', 'usd_millions_saar'
              ),
              (
                'bea-dpi-202606-v1',
                'macro.bea.disposable_personal_income',
                '2026-06-01', '22300.2', NULL, 1,
                '2026-08-23T12:00:00Z', '2026-08-23T12:00:00Z',
                '2026-06-30', 'usd_millions_saar'
              ),
              (
                'bea-pce-202606-v1',
                'macro.bea.personal_consumption_expenditures',
                '2026-06-01', '20900.3', NULL, 1,
                '2026-08-23T12:00:00Z', '2026-08-23T12:00:00Z',
                '2026-06-30', 'usd_millions_saar'
              );
            INSERT INTO macro_observations VALUES
              (
                'macro.federal_reserve_h41.total_assets_less_eliminations_wednesday',
                '2026-08-19', 'h41-total-20260819-v1'
              ),
              (
                'macro.federal_reserve_policy.iorb',
                '2026-08-20', 'iorb-20260820-v1'
              ),
              (
                'macro.chicagofed.nfci', '2026-08-21',
                'nfci-20260821-v1'
              ),
              (
                'macro.chicagofed.cfnai', '2026-07-01',
                'cfnai-202607-v1'
              ),
              (
                'macro.federal_reserve.industrial_production_total_sa',
                '2026-07-01', 'indpro-202607-v1'
              ),
              (
                'macro.bis.us_private_nonfinancial_credit_gap',
                '2026-04-01', 'bis-gap-2026q2-v1'
              ),
              (
                'macro.nyfed.cmdi.market', '2026-07-24',
                'cmdi-market-20260724-v1'
              ),
              (
                'macro.treasury_fiscal.daily.tga_closing_balance',
                '2026-08-20', 'tga-20260820-v1'
              ),
              (
                'macro.treasury_fiscal.daily.total_public_debt_outstanding',
                '2026-08-20', 'debt-total-20260820-v1'
              ),
              (
                'macro.treasury_fiscal.monthly.receipts',
                '2026-07-01', 'fiscal-receipts-202607-v1'
              ),
              (
                'macro.eia.weekly.lower_48_working_natural_gas_storage',
                '2026-08-14', 'eia-gas-20260814-v1'
              ),
              (
                'macro.eia.petroleum.weekly.us_total_motor_gasoline_ending_stocks',
                '2026-08-14', 'eia-gasoline-stocks-20260814-v1'
              ),
              (
                'macro.eia.petroleum.weekly.us_distillate_fuel_oil_ending_stocks',
                '2026-08-14', 'eia-distillate-stocks-20260814-v1'
              ),
              (
                'macro.eia.petroleum.weekly.us_finished_motor_gasoline_product_supplied',
                '2026-08-14', 'eia-gasoline-supplied-20260814-v1'
              ),
              (
                'macro.nber.us_recession_indicator',
                '2020-07-01', 'nber-recession-202007-v1'
              ),
              (
                'macro.bls.ppi_final_demand_sa',
                '2026-07-01', 'bls-ppi-202607-v1'
              ),
              (
                'macro.bls.employment_cost_index_total_compensation_civilian_sa',
                '2026-04-01', 'bls-eci-2026q2-v1'
              ),
              (
                'macro.bea.personal_income',
                '2026-06-01', 'bea-pi-202606-v1'
              ),
              (
                'macro.bea.disposable_personal_income',
                '2026-06-01', 'bea-dpi-202606-v1'
              ),
              (
                'macro.bea.personal_consumption_expenditures',
                '2026-06-01', 'bea-pce-202606-v1'
              );

            CREATE TABLE treasury_yield_curve_versions (
                curve_version_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                curve_date TEXT NOT NULL,
                curve_variant TEXT NOT NULL,
                tenor TEXT NOT NULL,
                series_id TEXT NOT NULL,
                yield_value TEXT,
                missing_reason TEXT,
                available_at TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                correction_sequence INTEGER NOT NULL
            );
            INSERT INTO treasury_yield_curve_versions VALUES
              (
                'curve-20260820-1m-v1', 'fmp', '2026-08-20', 'par_yield',
                '1M', 'macro.treasury.par_yield.1m', '4.20', NULL,
                '2026-08-20T22:00:00Z', '2026-08-20T22:00:00Z', 1
              ),
              (
                'curve-20260820-10y-v1', 'fmp', '2026-08-20', 'par_yield',
                '10Y', 'macro.treasury.par_yield.10y', '4.30', NULL,
                '2026-08-20T22:00:00Z', '2026-08-20T22:00:00Z', 1
              ),
              (
                'curve-20260820-10y-v2', 'fmp', '2026-08-20', 'par_yield',
                '10Y', 'macro.treasury.par_yield.10y', '4.40', NULL,
                '2026-08-21T12:00:00Z', '2026-08-21T12:00:00Z', 2
              ),
              (
                'curve-20260821-1m-v1', 'fmp', '2026-08-21', 'par_yield',
                '1M', 'macro.treasury.par_yield.1m', '4.10', NULL,
                '2026-08-21T22:00:00Z', '2026-08-21T22:00:00Z', 1
              );

            CREATE TABLE soma_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                as_of_date TEXT NOT NULL,
                completeness TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                run_id TEXT NOT NULL
            );
            CREATE TABLE soma_summary_components (
                snapshot_id TEXT NOT NULL,
                as_of_date TEXT NOT NULL,
                category TEXT NOT NULL,
                measure TEXT NOT NULL,
                value_text TEXT,
                missing_reason TEXT,
                unit TEXT NOT NULL,
                available_at TEXT NOT NULL
            );

            CREATE TABLE ingestion_runs (
                run_id TEXT PRIMARY KEY,
                dataset_id TEXT NOT NULL,
                command TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE TABLE ingestion_artifacts (
                artifact_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                dataset_id TEXT NOT NULL,
                content_sha256 TEXT NOT NULL
            );
            CREATE TABLE ingestion_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                dataset_id TEXT NOT NULL,
                semantic_identity TEXT NOT NULL,
                completeness TEXT NOT NULL,
                validation_state TEXT NOT NULL
            );
            CREATE TABLE fmp_economic_calendar_captures (
                capture_id TEXT PRIMARY KEY,
                request_start_date TEXT NOT NULL,
                request_end_date TEXT NOT NULL,
                response_sha256 TEXT NOT NULL,
                semantic_identity TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                run_id TEXT NOT NULL,
                artifact_id TEXT NOT NULL,
                snapshot_id TEXT NOT NULL
            );
            CREATE TABLE fmp_economic_calendar_rows (
                capture_id TEXT NOT NULL,
                source_row INTEGER NOT NULL,
                row_sha256 TEXT NOT NULL,
                raw_row_json TEXT NOT NULL,
                event_at TEXT NOT NULL,
                country TEXT NOT NULL,
                event_name TEXT NOT NULL,
                currency TEXT,
                unit TEXT,
                previous_json TEXT,
                estimate_json TEXT,
                actual_json TEXT,
                change_json TEXT,
                impact_json TEXT,
                change_percentage_json TEXT
            );
            CREATE TABLE fmp_economic_calendar_fetch_receipts (
                receipt_id TEXT PRIMARY KEY,
                request_start_date TEXT NOT NULL,
                request_end_date TEXT NOT NULL,
                response_sha256 TEXT NOT NULL,
                semantic_identity TEXT NOT NULL,
                source_semantic_identity TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                row_count INTEGER NOT NULL,
                run_id TEXT NOT NULL
            );
            CREATE TABLE fmp_economic_calendar_latest_response_cache (
                feed_id TEXT PRIMARY KEY,
                response_bytes BLOB NOT NULL,
                response_sha256 TEXT NOT NULL,
                semantic_identity TEXT NOT NULL,
                source_semantic_identity TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                captured_precision TEXT NOT NULL,
                request_start_date TEXT NOT NULL,
                request_end_date TEXT NOT NULL,
                normalization_version TEXT NOT NULL,
                persistence_version TEXT NOT NULL,
                row_count INTEGER NOT NULL,
                receipt_id TEXT NOT NULL
            );
            CREATE TABLE fmp_economic_calendar_raw_events (
                event_id TEXT PRIMARY KEY,
                event_at TEXT NOT NULL,
                country TEXT NOT NULL,
                event_name TEXT NOT NULL
            );
            CREATE TABLE fmp_economic_calendar_raw_event_versions (
                event_version_id TEXT PRIMARY KEY,
                event_id TEXT NOT NULL,
                receipt_id TEXT NOT NULL,
                source_row INTEGER NOT NULL,
                row_sha256 TEXT NOT NULL,
                raw_row_json TEXT NOT NULL,
                currency TEXT,
                unit TEXT,
                previous_json TEXT,
                estimate_json TEXT,
                actual_json TEXT,
                change_json TEXT,
                impact_json TEXT,
                change_percentage_json TEXT,
                captured_at TEXT NOT NULL,
                correction_sequence INTEGER NOT NULL
            );


            INSERT INTO ingestion_runs VALUES
              (
                'raw-run-1', 'macro.fmp.economic_calendar_evidence',
                'fmp.macro.us_economic_calendar_wholesale', 'succeeded'
              ),
              (
                'raw-run-2', 'macro.fmp.economic_calendar_evidence',
                'fmp.macro.us_economic_calendar_wholesale', 'succeeded'
              ),
              (
                'raw-run-pending', 'macro.fmp.economic_calendar_evidence',
                'fmp.macro.us_economic_calendar_wholesale', 'running'
              ),
              (
                'soma-run-1', 'fixture.macro.soma_summary',
                'nyfed.macro.soma_summary_history', 'succeeded'
              ),
              (
                'soma-run-2', 'fixture.macro.soma_summary',
                'nyfed.macro.soma_summary_history', 'succeeded'
              );
            INSERT INTO ingestion_artifacts VALUES
              (
                'raw-artifact-1', 'raw-run-1',
                'macro.fmp.economic_calendar_evidence',
                'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
              ),
              (
                'raw-artifact-2', 'raw-run-2',
                'macro.fmp.economic_calendar_evidence',
                'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
              ),
              (
                'raw-artifact-pending', 'raw-run-pending',
                'macro.fmp.economic_calendar_evidence',
                'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc'
              );
            INSERT INTO ingestion_snapshots VALUES
              (
                'raw-snapshot-1', 'raw-run-1',
                'macro.fmp.economic_calendar_evidence',
                '1111111111111111111111111111111111111111111111111111111111111111',
                'complete', 'validated'
              ),
              (
                'raw-snapshot-2', 'raw-run-2',
                'macro.fmp.economic_calendar_evidence',
                '2222222222222222222222222222222222222222222222222222222222222222',
                'complete', 'validated'
              ),
              (
                'raw-snapshot-pending', 'raw-run-pending',
                'macro.fmp.economic_calendar_evidence',
                '3333333333333333333333333333333333333333333333333333333333333333',
                'complete', 'validated'
              );
            INSERT INTO soma_snapshots VALUES
              (
                'soma-snapshot-1', '2026-08-20', 'complete',
                '2026-08-21T12:00:00Z', 'soma-run-1'
              ),
              (
                'soma-snapshot-2', '2026-08-20', 'complete',
                '2026-08-22T12:00:00Z', 'soma-run-2'
              );
            INSERT INTO soma_summary_components VALUES
              (
                'soma-snapshot-1', '2026-08-20', 'total', 'amount',
                '6300000', NULL, 'thousands_usd', '2026-08-21T12:00:00Z'
              ),
              (
                'soma-snapshot-1', '2026-08-20', 'agency_mbs', 'amount',
                '1900000', NULL, 'thousands_usd', '2026-08-21T12:00:00Z'
              ),
              (
                'soma-snapshot-2', '2026-08-20', 'total', 'amount',
                '6310000', NULL, 'thousands_usd', '2026-08-22T12:00:00Z'
              ),
              (
                'soma-snapshot-2', '2026-08-20', 'agency_mbs', 'amount',
                '1910000', NULL, 'thousands_usd', '2026-08-22T12:00:00Z'
              );

            INSERT INTO fmp_economic_calendar_captures VALUES
              (
                'raw-capture-1', '2026-08-01', '2026-08-10',
                'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                '1111111111111111111111111111111111111111111111111111111111111111',
                '2026-08-11T12:00:00Z', 'raw-run-1', 'raw-artifact-1', 'raw-snapshot-1'
              ),
              (
                'raw-capture-2', '2026-08-01', '2026-08-17',
                'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
                '2222222222222222222222222222222222222222222222222222222222222222',
                '2026-08-18T12:00:00Z', 'raw-run-2', 'raw-artifact-2', 'raw-snapshot-2'
              ),
              (
                'raw-capture-pending', '2026-08-01', '2026-08-17',
                'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',
                '3333333333333333333333333333333333333333333333333333333333333333',
                '2026-08-18T12:01:00Z', 'raw-run-pending',
                'raw-artifact-pending', 'raw-snapshot-pending'
              );
            INSERT INTO fmp_economic_calendar_rows VALUES
              (
                'raw-capture-1', 1,
                'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1',
                '{"actual":-23,"country":"US","event":"Non Farm Payrolls (Jul)","impact":"High","note":"<b>jobs</b>"}',
                '2026-08-07 12:30:00', 'US', 'Non Farm Payrolls (Jul)', 'USD', 'K',
                '14', '80', '-23', '-37', '"High"', NULL
              ),
              (
                'raw-capture-1', 2,
                'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa2',
                '{"actual":4.1,"country":"US","event":"Unemployment Rate (Jul)","impact":"High"}',
                '2026-08-07 12:30:00', 'US', 'Unemployment Rate (Jul)', 'USD', '%',
                '4.1', '4.2', '4.1', '-0.1', '"High"', NULL
              ),
              (
                'raw-capture-2', 1,
                'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb1',
                '{"actual":-22,"country":"US","event":"Non Farm Payrolls (Jul)","impact":"High","note":"jobs revision"}',
                '2026-08-07 12:30:00', 'US', 'Non Farm Payrolls (Jul)', 'USD', 'K',
                '14', '80', '-22', '-36', '"High"', NULL
              ),
              (
                'raw-capture-2', 2,
                'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb2',
                '{"actual":0.2,"country":"US","event":"CPI MoM (Jul)","impact":"Medium"}',
                '2026-08-13 12:30:00', 'US', 'CPI MoM (Jul)', 'USD', '%',
                '0.3', '0.2', '0.2', '-0.1', '"Medium"', NULL
              ),
              (
                'raw-capture-2', 3,
                'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb3',
                '{"actual":12,"country":"CA","event":"Employment Change","impact":"Low"}',
                '2026-08-14 12:30:00', 'CA', 'Employment Change', 'CAD', 'K',
                '8', '10', '12', '4', '"Low"', NULL
              ),
              (
                'raw-capture-pending', 1,
                'ccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc1',
                '{"country":"US","event":"Hidden Pending Event","impact":"High"}',
                '2026-08-15 12:30:00', 'US', 'Hidden Pending Event', 'USD', NULL,
                NULL, NULL, NULL, NULL, '"High"', NULL
              );

            CREATE TABLE stage11_eia_retail_observation_versions (
                version_id TEXT PRIMARY KEY,
                canonical_series_id TEXT NOT NULL,
                metric TEXT NOT NULL,
                period TEXT NOT NULL,
                state_id TEXT NOT NULL,
                sector_id TEXT NOT NULL,
                value_text TEXT,
                unit TEXT NOT NULL,
                correction_sequence INTEGER NOT NULL,
                available_at TEXT NOT NULL,
                captured_at TEXT NOT NULL
            );
            CREATE TABLE stage11_eia_retail_observations (
                canonical_series_id TEXT NOT NULL,
                period TEXT NOT NULL,
                state_id TEXT NOT NULL,
                sector_id TEXT NOT NULL,
                current_version_id TEXT NOT NULL
            );
            INSERT INTO stage11_eia_retail_observation_versions VALUES (
                'eia-retail-sales-202606-v1',
                'macro.eia.electricity.retail_sales',
                'sales', '2026-06', 'US', 'ALL', '350.1',
                'million kilowatthours', 1,
                '2026-08-23T12:00:00Z', '2026-08-23T12:00:00Z'
            );
            INSERT INTO stage11_eia_retail_observations VALUES (
                'macro.eia.electricity.retail_sales',
                '2026-06', 'US', 'ALL', 'eia-retail-sales-202606-v1'
            );

            CREATE TABLE stage11_eia_weekly_observation_versions (
                version_id TEXT PRIMARY KEY,
                canonical_series_id TEXT NOT NULL,
                provider_series_id TEXT NOT NULL,
                period TEXT NOT NULL,
                value_text TEXT NOT NULL,
                unit TEXT NOT NULL,
                correction_sequence INTEGER NOT NULL,
                available_at TEXT NOT NULL,
                captured_at TEXT NOT NULL
            );
            CREATE TABLE stage11_eia_weekly_observations (
                canonical_series_id TEXT NOT NULL,
                period TEXT NOT NULL,
                current_version_id TEXT NOT NULL
            );
            INSERT INTO stage11_eia_weekly_observation_versions VALUES (
                'eia-weekly-stock-20260814-v1',
                'macro.eia.weekly.petroleum_stock',
                'PET.WCESTUS1.W', '2026-08-14', '425000',
                'Thousand Barrels', 1,
                '2026-08-23T12:00:00Z', '2026-08-23T12:00:00Z'
            );
            INSERT INTO stage11_eia_weekly_observations VALUES (
                'macro.eia.weekly.petroleum_stock',
                '2026-08-14', 'eia-weekly-stock-20260814-v1'
            );
            """
        )
        cache_body = dumps_strict(
            [
                {
                    "date": "2026-08-07 12:30:00",
                    "country": "US",
                    "event": "Non Farm Payrolls (Jul)",
                    "currency": "USD",
                    "unit": "K",
                    "previous": 14,
                    "estimate": 80,
                    "actual": -22,
                    "change": -36,
                    "impact": "High",
                    "note": "<b>jobs revision</b>",
                },
                {
                    "date": "2026-08-07 12:30:00",
                    "country": "US",
                    "event": "Unemployment Rate (Jul)",
                    "currency": "USD",
                    "unit": "%",
                    "previous": 4.1,
                    "estimate": 4.2,
                    "actual": 4.1,
                    "change": -0.1,
                    "impact": "High",
                },
                {
                    "date": "2026-08-13 12:30:00",
                    "country": "US",
                    "event": "CPI MoM (Jul)",
                    "currency": "USD",
                    "unit": "%",
                    "previous": 0.3,
                    "estimate": 0.2,
                    "actual": 0.2,
                    "change": -0.1,
                    "impact": "Medium",
                },
                {
                    "date": "2026-08-14 12:30:00",
                    "country": "CA",
                    "event": "Employment Change",
                    "currency": "CAD",
                    "unit": "K",
                    "previous": 8,
                    "estimate": 10,
                    "actual": 12,
                    "change": 4,
                    "impact": "Low",
                },
            ]
        ).encode("utf-8")
        cache_capture = parse_fmp_us_calendar_wholesale(
            cache_body,
            captured_at="2026-08-19T12:00:00Z",
            start_date="2026-08-01",
            end_date="2026-08-17",
        )
        transition_identity = "4" * 64
        connection.execute(
            """
            INSERT INTO ingestion_runs VALUES (
                'incremental-run-1',
                'macro.fmp.economic_calendar_incremental_evidence',
                'fmp.macro.us_economic_calendar_wholesale',
                'succeeded'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO fmp_economic_calendar_fetch_receipts VALUES (
                'incremental-receipt-1', ?, ?, ?, ?, ?, ?, ?, 'incremental-run-1'
            )
            """,
            (
                cache_capture.request_start_date,
                cache_capture.request_end_date,
                cache_capture.response_sha256,
                transition_identity,
                cache_capture.semantic_identity,
                cache_capture.captured_at,
                len(cache_capture.rows),
            ),
        )
        connection.execute(
            """
            INSERT INTO fmp_economic_calendar_latest_response_cache VALUES (
                'fmp_us', ?, ?, ?, ?, ?, 'datetime', ?, ?,
                'fmp_us_calendar_wholesale_evidence_v1',
                'fmp_us_calendar_incremental_events_v2', ?,
                'incremental-receipt-1'
            )
            """,
            (
                cache_capture.response_bytes,
                cache_capture.response_sha256,
                transition_identity,
                cache_capture.semantic_identity,
                cache_capture.captured_at,
                cache_capture.request_start_date,
                cache_capture.request_end_date,
                len(cache_capture.rows),
            ),
        )
        first = cache_capture.rows[0]
        connection.execute(
            """
            INSERT INTO fmp_economic_calendar_raw_events VALUES (
                'incremental-event-nfp', ?, ?, ?
            )
            """,
            (first.event_at, first.country, first.event_name),
        )
        connection.execute(
            """
            INSERT INTO fmp_economic_calendar_raw_event_versions VALUES (
                'incremental-version-nfp-1', 'incremental-event-nfp',
                'incremental-receipt-1', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                1
            )
            """,
            (
                first.source_row,
                first.row_sha256,
                first.raw_row_json,
                first.currency,
                first.unit,
                first.previous_json,
                first.estimate_json,
                first.actual_json,
                first.change_json,
                first.impact_json,
                first.change_percentage_json,
                cache_capture.captured_at,
            ),
        )
        connection.commit()
        connection.close()

    @staticmethod
    def _sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_all_fixed_views_return_rows_without_mutating_stores(self) -> None:
        before = (
            self._sha256(self.market),
            self._sha256(self.macro),
            self._sha256(self.company),
        )
        routes = (
            ("/api/rows?view=market-prices&symbol=AAPL", "AAPL"),
            ("/api/rows?view=market-instruments&search=Apple", "Apple Inc."),
            (
                "/api/rows?view=company-fundamentals&metric=revenue"
                "&period_end=2024-09-28",
                "383285000000",
            ),
            (
                "/api/rows?view=macro-current&series=macro.gdp.real_qoq_saar_pct",
                "2026Q2",
            ),
            (
                "/api/rows?view=macro-vintages&series=macro.gdp.real_qoq_saar_pct&period=2026Q2",
                "2026-07-30-advance",
            ),
            (
                "/api/rows?view=treasury-curve&tenor=10Y"
                "&start_date=2026-08-20&end_date=2026-08-20",
                "4.40",
            ),
            (
                "/api/rows?view=overnight-rates&rate=EFFR"
                "&start_date=2026-08-20&end_date=2026-08-20",
                "3.63",
            ),
            (
                "/api/rows?view=repo-facilities&facility=ON_RRP"
                "&start_date=2026-08-20&end_date=2026-08-20",
                "25000000000",
            ),
            (
                "/api/rows?view=soma-summary&component=total"
                "&start_date=2026-08-20&end_date=2026-08-20",
                "6310000",
            ),
            (
                "/api/rows?view=price-wage-productivity"
                "&series=macro.bls.ppi_final_demand_sa",
                "150.1",
            ),
            (
                "/api/rows?view=price-wage-productivity"
                "&series=macro.bls."
                "employment_cost_index_total_compensation_civilian_sa",
                "168.1",
            ),
            (
                "/api/rows?view=personal-income-outlays"
                "&series=macro.bea.personal_income",
                "26500.1",
            ),
            (
                "/api/rows?view=h41-liquidity"
                "&series=macro.federal_reserve_h41."
                "total_assets_less_eliminations_wednesday",
                "6600001",
            ),
            (
                "/api/rows?view=policy-rates"
                "&series=macro.federal_reserve_policy.iorb",
                "3.65",
            ),
            (
                "/api/rows?view=financial-conditions"
                "&series=macro.chicagofed.nfci",
                "-0.28",
            ),
            (
                "/api/rows?view=national-activity"
                "&series=macro.chicagofed.cfnai",
                "0.14",
            ),
            (
                "/api/rows?view=industrial-production"
                "&series=macro.federal_reserve."
                "industrial_production_total_sa",
                "102.9939",
            ),
            (
                "/api/rows?view=bis-credit"
                "&series=macro.bis.us_private_nonfinancial_credit_gap",
                "-2.7",
            ),
            (
                "/api/rows?view=treasury-cash",
                "765432",
            ),
            (
                "/api/rows?view=treasury-debt",
                "37000123456789.01",
            ),
            (
                "/api/rows?view=fiscal-balance",
                "334010",
            ),
            (
                "/api/rows?view=natural-gas-storage",
                "3199",
            ),
            (
                "/api/rows?view=crude-oil-stocks"
                "&start_date=2026-08-14&end_date=2026-08-14",
                "425000",
            ),
            (
                "/api/rows?view=petroleum-fundamentals"
                "&series=macro.eia.petroleum.weekly."
                "us_total_motor_gasoline_ending_stocks",
                "235000",
            ),
            (
                "/api/rows?view=recession-chronology",
                "2020-07-01",
            ),
            (
                "/api/rows?view=fmp-economic-calendar&country=US&priority=High"
                "&event_name=Non%20Farm&keyword=jobs&start_date=2026-08-07"
                "&end_date=2026-08-07",
                "Non Farm Payrolls",
            ),
        )
        for route, expected in routes:
            with self.subTest(route=route):
                response = self.application.handle("GET", route)
                self.assertEqual(response.status, 200)
                payload = loads_strict(response.body)
                self.assertEqual(payload["execution"], "read_only")
                self.assertIn(expected, str(payload["result"]["rows"]))

        treasury = self.application.handle(
            "GET",
            "/api/rows?view=treasury-curve&tenor=10Y"
            "&start_date=2026-08-20&end_date=2026-08-20",
        )
        treasury_result = loads_strict(treasury.body)["result"]
        self.assertEqual(treasury_result["total"], 1)
        self.assertEqual(treasury_result["rows"][0]["yield_percent"], "4.40")
        self.assertEqual(treasury_result["rows"][0]["correction"], 2)

        overnight = self.application.handle(
            "GET",
            "/api/rows?view=overnight-rates"
            "&start_date=2026-08-20&end_date=2026-08-20",
        )
        overnight_result = loads_strict(overnight.body)["result"]
        self.assertEqual(overnight_result["total"], 2)
        self.assertEqual(
            {row["rate"] for row in overnight_result["rows"]},
            {"EFFR", "SOFR"},
        )

        facilities = self.application.handle(
            "GET",
            "/api/rows?view=repo-facilities"
            "&start_date=2026-08-20&end_date=2026-08-20",
        )
        facilities_result = loads_strict(facilities.body)["result"]
        self.assertEqual(facilities_result["total"], 2)
        self.assertEqual(
            {row["facility"] for row in facilities_result["rows"]},
            {"ON_RRP", "SRF"},
        )

        soma = self.application.handle(
            "GET",
            "/api/rows?view=soma-summary"
            "&start_date=2026-08-20&end_date=2026-08-20",
        )
        soma_result = loads_strict(soma.body)["result"]
        self.assertEqual(soma_result["total"], 2)
        self.assertEqual(
            {row["amount_thousands_usd"] for row in soma_result["rows"]},
            {"1910000", "6310000"},
        )

        soma_page = self.application.handle(
            "GET",
            "/?view=soma-summary&component=total"
            "&start_date=2026-08-20&end_date=2026-08-20",
        )
        soma_html = soma_page.body.decode("utf-8")
        self.assertEqual(soma_page.status, 200)
        self.assertIn("SOMA summary", soma_html)
        self.assertIn("6310000", soma_html)
        self.assertIn('name="component"', soma_html)

        official_views = {
            "personal-income-outlays": (
                "macro.bea.personal_consumption_expenditures",
                "20900.3",
            ),
            "price-wage-productivity": (
                "macro.bls."
                "employment_cost_index_total_compensation_civilian_sa",
                "168.1",
            ),
            "h41-liquidity": (
                "macro.federal_reserve_h41."
                "total_assets_less_eliminations_wednesday",
                "6600001",
            ),
            "policy-rates": (
                "macro.federal_reserve_policy.iorb",
                "3.65",
            ),
            "financial-conditions": (
                "macro.chicagofed.nfci",
                "-0.28",
            ),
            "national-activity": (
                "macro.chicagofed.cfnai",
                "0.14",
            ),
            "industrial-production": (
                "macro.federal_reserve.industrial_production_total_sa",
                "102.9939",
            ),
            "bis-credit": (
                "macro.bis.us_private_nonfinancial_credit_gap",
                "-2.7",
            ),
            "credit-market-distress": (
                "macro.nyfed.cmdi.market",
                "0.15",
            ),
            "treasury-cash": (
                "macro.treasury_fiscal.daily.tga_closing_balance",
                "765432",
            ),
            "treasury-debt": (
                "macro.treasury_fiscal.daily.total_public_debt_outstanding",
                "37000123456789.01",
            ),
            "fiscal-balance": (
                "macro.treasury_fiscal.monthly.receipts",
                "334010",
            ),
            "natural-gas-storage": (
                "macro.eia.weekly.lower_48_working_natural_gas_storage",
                "3199",
            ),
            "petroleum-fundamentals": (
                "macro.eia.petroleum.weekly."
                "us_finished_motor_gasoline_product_supplied",
                "9100",
            ),
            "recession-chronology": (
                "macro.nber.us_recession_indicator",
                "1",
            ),
        }
        for view, (series_id, value) in official_views.items():
            with self.subTest(view=view):
                response = self.application.handle(
                    "GET",
                    f"/api/rows?view={view}&series={series_id}",
                )
                result = loads_strict(response.body)["result"]
                self.assertEqual(response.status, 200)
                self.assertEqual(result["total"], 1)
                self.assertEqual(result["rows"][0]["value"], value)

        bea = self.application.handle(
            "GET", "/api/rows?view=personal-income-outlays"
        )
        bea_result = loads_strict(bea.body)["result"]
        self.assertEqual(bea.status, 200)
        self.assertEqual(bea_result["total"], 3)
        self.assertEqual(
            {row["series"] for row in bea_result["rows"]},
            {
                "macro.bea.personal_income",
                "macro.bea.disposable_personal_income",
                "macro.bea.personal_consumption_expenditures",
            },
        )
        bea_page = self.application.handle(
            "GET", "/?view=personal-income-outlays"
        )
        bea_html = bea_page.body.decode("utf-8")
        self.assertEqual(bea_page.status, 200)
        self.assertIn("Personal income &amp; outlays", bea_html)
        self.assertIn("Personal consumption expenditures", bea_html)
        self.assertIn('name="series"', bea_html)

        debt_page = self.application.handle(
            "GET",
            "/?view=treasury-debt"
            "&series=macro.treasury_fiscal.daily."
            "total_public_debt_outstanding",
        )
        debt_html = debt_page.body.decode("utf-8")
        self.assertEqual(debt_page.status, 200)
        self.assertIn("Treasury debt", debt_html)
        self.assertIn("37000123456789.01", debt_html)
        self.assertIn('name="series"', debt_html)

        fiscal_page = self.application.handle(
            "GET",
            "/?view=fiscal-balance"
            "&series=macro.treasury_fiscal.monthly.receipts",
        )
        fiscal_html = fiscal_page.body.decode("utf-8")
        self.assertEqual(fiscal_page.status, 200)
        self.assertIn("Federal fiscal balance", fiscal_html)
        self.assertIn("334010", fiscal_html)
        self.assertIn('name="series"', fiscal_html)

        bls_page = self.application.handle(
            "GET",
            "/?view=price-wage-productivity"
            "&series=macro.bls.ppi_final_demand_sa",
        )
        bls_html = bls_page.body.decode("utf-8")
        self.assertEqual(bls_page.status, 200)
        self.assertIn("Prices, wages &amp; productivity", bls_html)
        self.assertIn(
            "Producer Price Index - Final Demand, seasonally adjusted",
            bls_html,
        )

        eci_page = self.application.handle(
            "GET",
            "/?view=price-wage-productivity"
            "&series=macro.bls."
            "employment_cost_index_total_compensation_civilian_sa",
        )
        eci_html = eci_page.body.decode("utf-8")
        self.assertEqual(eci_page.status, 200)
        self.assertIn("Employment Cost Index", eci_html)
        self.assertIn("168.1", eci_html)

        conditions_page = self.application.handle(
            "GET",
            "/?view=financial-conditions&series=macro.chicagofed.nfci",
        )
        conditions_html = conditions_page.body.decode("utf-8")
        self.assertEqual(conditions_page.status, 200)
        self.assertIn("Financial conditions", conditions_html)
        self.assertIn('name="series"', conditions_html)
        self.assertIn("Chicago Fed National Financial Conditions Index", conditions_html)

        activity_page = self.application.handle(
            "GET",
            "/?view=national-activity&series=macro.chicagofed.cfnai",
        )
        activity_html = activity_page.body.decode("utf-8")
        self.assertEqual(activity_page.status, 200)
        self.assertIn("National activity", activity_html)
        self.assertIn("Chicago Fed National Activity Index", activity_html)
        self.assertIn("0.14", activity_html)

        production_page = self.application.handle(
            "GET",
            "/?view=industrial-production"
            "&series=macro.federal_reserve."
            "industrial_production_total_sa",
        )
        production_html = production_page.body.decode("utf-8")
        self.assertEqual(production_page.status, 200)
        self.assertIn("Industrial production", production_html)
        self.assertIn("102.9939", production_html)

        cmdi_page = self.application.handle(
            "GET",
            "/?view=credit-market-distress"
            "&series=macro.nyfed.cmdi.market",
        )
        cmdi_html = cmdi_page.body.decode("utf-8")
        self.assertEqual(cmdi_page.status, 200)
        self.assertIn("Corporate bond distress", cmdi_html)
        self.assertIn('name="series"', cmdi_html)
        self.assertIn(
            "NY Fed Corporate Bond Market Distress Index", cmdi_html
        )

        surprise = SimpleNamespace(
            event_at="2026-07-30T12:30:00Z",
            reference_period="2026Q2",
            kind="us_gdp_real_qoq_saar_advance",
            consensus=SimpleNamespace(),
            fmp_actual=None,
            official_actual=None,
            surprise=None,
            unit="percent",
            actual_source="bea_gdp_vintage",
            consensus_mapping_basis="exact_bea_advance_date",
            status="missing_consensus",
            official_version_id="gdp-official-version",
            official_prior_version_id=None,
            release_stage="advance",
            is_fallback=False,
            coalesced_event_version_ids=(),
        )
        surprise.consensus = None
        with patch("quant_data.canonical_inspector.MacroReleaseSurpriseRepository") as repository:
            repository.return_value.query.return_value = (surprise,)
            response = self.application.handle(
                "GET",
                "/api/rows?view=macro-surprises&stage=advance",
            )
        self.assertEqual(response.status, 200)
        surprise_row = loads_strict(response.body)["result"]["rows"][0]
        self.assertEqual(surprise_row["reference_period"], "2026Q2")
        self.assertEqual(surprise_row["official_version_id"], "gdp-official-version")
        self.assertIsNone(surprise_row["official_prior_version_id"])
        self.assertEqual(surprise_row["release_stage"], "advance")
        self.assertFalse(surprise_row["is_fallback"])
        self.assertEqual(
            before,
            (
                self._sha256(self.market),
                self._sha256(self.macro),
                self._sha256(self.company),
            ),
        )

    def test_new_macro_series_are_visible_in_fixed_ui_views(self) -> None:
        before = (self._sha256(self.market), self._sha256(self.macro))

        electricity = self.application.handle(
            "GET",
            "/api/rows?view=electricity-retail"
            "&series=macro.eia.electricity.retail_sales"
            "&start_period=2026-01&end_period=2026-12",
        )
        result = loads_strict(electricity.body)["result"]
        self.assertEqual(electricity.status, 200)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["rows"][0]["value"], "350.1")
        self.assertEqual(
            result["rows"][0]["unit"],
            "million kilowatthours",
        )

        petroleum = self.application.handle(
            "GET",
            "/api/rows?view=crude-oil-stocks"
            "&start_date=2026-08-14&end_date=2026-08-14",
        )
        petroleum_result = loads_strict(petroleum.body)["result"]
        self.assertEqual(petroleum.status, 200)
        self.assertEqual(petroleum_result["total"], 1)
        self.assertEqual(petroleum_result["rows"][0]["value"], "425000")
        self.assertEqual(
            petroleum_result["rows"][0]["unit"],
            "Thousand Barrels",
        )

        rates = loads_strict(
            self.application.handle(
                "GET", "/api/rows?view=overnight-rates"
            ).body
        )["result"]
        self.assertIn("value", rates["columns"])
        self.assertIn("unit", rates["columns"])
        self.assertNotIn("rate_percent", rates["columns"])

        rates_html = self.application.handle(
            "GET", "/?view=overnight-rates"
        ).body.decode("utf-8")
        self.assertIn("SOFR Transaction Volume", rates_html)
        self.assertIn("SOFR 180-Day Compounded Average", rates_html)

        vintage_html = self.application.handle(
            "GET", "/?view=macro-vintages"
        ).body.decode("utf-8")
        self.assertIn("macro.gdi.real_qoq_saar_pct", vintage_html)
        self.assertIn("macro.gdi.nominal_billions", vintage_html)

        conditions_html = self.application.handle(
            "GET", "/?view=financial-conditions"
        ).body.decode("utf-8")
        self.assertIn("National Financial Conditions Index risk component", conditions_html)
        self.assertIn("National Financial Conditions Index credit component", conditions_html)
        self.assertIn("National Financial Conditions Index leverage component", conditions_html)

        electricity_html = self.application.handle(
            "GET", "/?view=electricity-retail"
        ).body.decode("utf-8")
        self.assertIn("Electricity retail", electricity_html)
        self.assertIn("Electricity retail customers", electricity_html)

        petroleum_html = self.application.handle(
            "GET", "/?view=crude-oil-stocks"
        ).body.decode("utf-8")
        self.assertIn("Crude oil stocks", petroleum_html)
        self.assertIn("425000", petroleum_html)

        fundamentals_html = self.application.handle(
            "GET", "/?view=petroleum-fundamentals"
        ).body.decode("utf-8")
        self.assertIn("Petroleum fundamentals", fundamentals_html)
        self.assertIn("U.S. ending stocks of total motor gasoline", fundamentals_html)
        self.assertIn("U.S. ending stocks of distillate fuel oil", fundamentals_html)
        self.assertIn(
            "U.S. product supplied of finished motor gasoline",
            fundamentals_html,
        )
        self.assertEqual(
            before,
            (self._sha256(self.market), self._sha256(self.macro)),
        )

    def test_spy_options_view_is_fixed_filterable_and_read_only(self) -> None:
        before = (self._sha256(self.market), self._sha256(self.macro))
        target = (
            "/api/rows?view=spy-options&option_type=call&state=present"
            "&expiration=2026-09-25"
        )
        response = self.application.handle("GET", target)
        self.assertEqual(response.status, 200)
        payload = loads_strict(response.body)
        self.assertEqual(payload["execution"], "read_only")
        result = payload["result"]
        self.assertEqual(result["total"], 1)
        row = result["rows"][0]
        self.assertEqual(row["capture_id"], "capture-current")
        self.assertEqual(row["feed"], "alpaca_indicative")
        self.assertEqual(row["contract_symbol"], "SPY260925C00650000")
        self.assertEqual(row["bid_price"], Decimal("4.1"))
        self.assertEqual(row["implied_volatility"], Decimal("0.205"))
        self.assertEqual(row["open_interest"], 12345)
        self.assertEqual(row["close_price"], Decimal("4.05"))
        self.assertEqual(row["underlying_last_price"], Decimal("645.0"))
        self.assertNotIn("SPY260919C00640000", str(result["rows"]))
        self.assertEqual(row["quote_at"], "2026-08-25T19:54:00.123456789Z")
        self.assertEqual(row["trade_at"], "2026-08-25T19:54:00.123456788Z")
        self.assertEqual(row["underlying_quote_at"], "2026-08-25T19:54:00.123456700Z")

        document = self.application.handle(
            "GET", "/?view=spy-options"
        ).body.decode("utf-8")
        self.assertIn("SPY options", document)
        self.assertIn('name="option_type"', document)
        self.assertIn('name="state"', document)
        self.assertIn('name="expiration"', document)

        for invalid in (
            "/api/rows?view=spy-options&option_type=spread",
            "/api/rows?view=spy-options&state=stale",
            "/api/rows?view=spy-options&expiration=2026-13-40",
            "/api/rows?view=spy-options&relation=sqlite_master",
        ):
            with self.subTest(invalid=invalid):
                invalid_response = self.application.handle("GET", invalid)
                self.assertEqual(invalid_response.status, 400)

        self.assertEqual(
            before,
            (self._sha256(self.market), self._sha256(self.macro)),
        )

    def test_options_surfaces_view_is_fixed_filterable_and_read_only(self) -> None:
        before = (self._sha256(self.market), self._sha256(self.macro))
        response = self.application.handle("GET", "/api/rows?view=options-surfaces")
        self.assertEqual(response.status, 200)
        payload = loads_strict(response.body)
        self.assertEqual(payload["execution"], "read_only")
        result = payload["result"]
        self.assertEqual(result["total"], 6)
        self.assertIn("target_dtes", result["columns"])
        self.assertIn("input_state", result["columns"])
        self.assertIn("input_missing_reason", result["columns"])
        self.assertIn("open_interest_state", result["columns"])
        self.assertIn("open_interest_missing_reason", result["columns"])
        self.assertIn("close_price_state", result["columns"])
        self.assertIn("close_price_missing_reason", result["columns"])
        self.assertIn("underlying_quote_state", result["columns"])
        self.assertIn("underlying_quote_missing_reason", result["columns"])
        self.assertIn("rate_curve_state", result["columns"])
        self.assertIn("rate_curve_missing_reason", result["columns"])
        self.assertIn("dividend_set_state", result["columns"])
        self.assertIn("dividend_set_missing_reason", result["columns"])
        qqq_rows = [
            row for row in result["rows"] if row["underlying_symbol"] == "QQQ"
        ]
        self.assertEqual(len(qqq_rows), 2)
        self.assertTrue(
            all(row["capture_id"] == "capture-qqq-current" for row in qqq_rows)
        )
        self.assertTrue(all(row["target_dtes"] == [7, 14] for row in qqq_rows))
        qqq_call = next(
            row
            for row in qqq_rows
            if row["contract_symbol"] == "QQQ260926C00575000"
        )
        self.assertEqual(qqq_call["open_interest"], 54321)
        self.assertEqual(qqq_call["open_interest_as_of_date"], "2026-08-25")
        self.assertEqual(qqq_call["open_interest_state"], "present")
        self.assertIsNone(qqq_call["open_interest_missing_reason"])
        self.assertEqual(qqq_call["close_price"], Decimal("8.25"))
        self.assertEqual(qqq_call["close_price_trade_date"], "2026-08-25")
        self.assertEqual(qqq_call["close_price_state"], "present")
        self.assertIsNone(qqq_call["close_price_missing_reason"])
        self.assertEqual(qqq_call["input_state"], "missing")
        self.assertEqual(
            qqq_call["input_missing_reason"],
            "synchronized_model_inputs_not_collected_in_live_etf_grid",
        )
        self.assertEqual(qqq_call["underlying_spot_price"], "575")
        self.assertEqual(qqq_call["underlying_bid_price"], Decimal("574.9"))
        self.assertIsNone(qqq_call["underlying_last_price"])
        self.assertEqual(qqq_call["underlying_quote_state"], "present")
        self.assertIsNone(qqq_call["underlying_quote_missing_reason"])
        self.assertEqual(qqq_call["rate_curve_state"], "missing")
        self.assertEqual(
            qqq_call["rate_curve_missing_reason"],
            "not_collected_in_live_etf_grid",
        )
        self.assertEqual(qqq_call["dividend_set_state"], "missing")
        self.assertEqual(
            qqq_call["dividend_set_missing_reason"],
            "not_collected_in_live_etf_grid",
        )

        qqq_target = loads_strict(
            self.application.handle(
                "GET",
                "/api/rows?view=options-surfaces&underlying=QQQ&target_dte=7",
            ).body
        )["result"]
        self.assertEqual(qqq_target["total"], 2)
        self.assertEqual(
            {row["contract_symbol"] for row in qqq_target["rows"]},
            {"QQQ260926C00575000", "QQQ260926P00570000"},
        )
        self.assertNotIn("capture-qqq-old", str(qqq_target["rows"]))

        filtered = loads_strict(
            self.application.handle(
                "GET",
                "/api/rows?view=options-surfaces&underlying=QQQ&target_dte=14"
                "&option_type=put&state=missing&expiration=2026-09-26",
            ).body
        )["result"]
        self.assertEqual(filtered["total"], 1)
        missing_row = filtered["rows"][0]
        self.assertEqual(missing_row["missing_reason"], "quote_unavailable")
        self.assertEqual(missing_row["open_interest_state"], "missing")
        self.assertEqual(
            missing_row["open_interest_missing_reason"], "source_not_provided"
        )
        self.assertEqual(missing_row["close_price_state"], "missing")
        self.assertEqual(
            missing_row["close_price_missing_reason"], "source_not_provided"
        )

        legacy = loads_strict(
            self.application.handle(
                "GET",
                "/api/rows?view=options-surfaces&underlying=SPY&target_dte=30"
                "&option_type=call&state=present&expiration=2026-09-25",
            ).body
        )["result"]
        self.assertEqual(legacy["total"], 1)
        self.assertEqual(legacy["rows"][0]["target_dtes"], [30])
        self.assertEqual(legacy["rows"][0]["capture_id"], "capture-current")

        document = self.application.handle(
            "GET", "/?view=options-surfaces&underlying=QQQ&target_dte=14"
        ).body.decode("utf-8")
        self.assertIn("Options surfaces", document)
        self.assertIn('name="underlying"', document)
        self.assertIn('name="target_dte"', document)
        self.assertIn('<option value="QQQ" selected>', document)
        self.assertIn('<option value="365">365 DTE</option>', document)

        for invalid in (
            "/api/rows?view=options-surfaces&underlying=spy",
            "/api/rows?view=options-surfaces&underlying=SPY%27%20OR%201%3D1--",
            "/api/rows?view=options-surfaces&target_dte=4",
            "/api/rows?view=options-surfaces&target_dte=7%20OR%201%3D1",
            "/api/rows?view=options-surfaces&option_type=spread",
            "/api/rows?view=options-surfaces&state=stale",
            "/api/rows?view=options-surfaces&expiration=2026-13-40",
            "/api/rows?view=options-surfaces&relation=sqlite_master",
        ):
            with self.subTest(invalid=invalid):
                self.assertEqual(self.application.handle("GET", invalid).status, 400)

        self.assertEqual(
            before,
            (self._sha256(self.market), self._sha256(self.macro)),
        )

    def test_options_surfaces_rejects_empty_stored_target_dtes(self) -> None:
        connection = sqlite3.connect(self.market)
        connection.execute(
            "UPDATE option_surface_captures SET request_scope_json=? "
            "WHERE capture_id='capture-qqq-current'",
            (
                '{"underlying_symbol":"QQQ","target_dtes":[],"selected_expiration":'
                '"2026-09-26","spot_price":"575"}',
            ),
        )
        connection.commit()
        connection.close()
        response = self.application.handle(
            "GET", "/api/rows?view=options-surfaces&underlying=QQQ"
        )
        self.assertEqual(response.status, 503)
        payload = loads_strict(response.body)
        self.assertEqual(payload["error"]["code"], "store_unavailable")

    def test_company_fundamentals_view_is_fixed_filterable_and_read_only(self) -> None:
        before = self._sha256(self.company)
        target = (
            "/api/rows?view=company-fundamentals&metric=revenue"
            "&period_end=2024-09-28"
        )
        response = self.application.handle("GET", target)
        self.assertEqual(response.status, 200)
        payload = loads_strict(response.body)
        self.assertEqual(payload["execution"], "read_only")
        result = payload["result"]
        self.assertEqual(result["total"], 1)
        row = result["rows"][0]
        self.assertEqual(row["cik"], "0000320193")
        self.assertEqual(row["legal_name"], "Apple Inc.")
        self.assertEqual(row["metric"], "revenue")
        self.assertEqual(row["metric_name"], "Revenue")
        self.assertEqual(row["value"], "383285000000")
        self.assertEqual(row["version_sequence"], 2)
        self.assertEqual(row["filing_form"], "10-K")
        self.assertEqual(row["filing_date"], "2024-11-01")
        self.assertEqual(row["taxonomy"], "us-gaap")
        self.assertEqual(
            row["source_concept"],
            "RevenueFromContractWithCustomerExcludingAssessedTax",
        )
        self.assertNotIn("380000000000", str(result["rows"]))

        all_metrics = loads_strict(
            self.application.handle(
                "GET", "/api/rows?view=company-fundamentals&direction=asc"
            ).body
        )["result"]
        self.assertEqual(all_metrics["total"], 3)
        self.assertEqual(
            {item["metric"] for item in all_metrics["rows"]},
            {"revenue", "total_assets"},
        )

        document = self.application.handle(
            "GET", "/?view=company-fundamentals"
        ).body.decode("utf-8")
        self.assertIn("Company fundamentals", document)
        self.assertIn("All reviewed metrics", document)
        self.assertIn('name="metric"', document)
        self.assertIn('name="cik"', document)
        self.assertIn('name="period_end"', document)
        self.assertIn("Browse retained observations with their original values and evidence.", document)

        microsoft = loads_strict(
            self.application.handle(
                "GET", "/api/rows?view=company-fundamentals&cik=0000789019"
            ).body
        )["result"]
        self.assertEqual(microsoft["total"], 1)
        self.assertEqual(microsoft["query"]["cik"], "0000789019")
        self.assertEqual(microsoft["rows"][0]["legal_name"], "Microsoft Corporation")

        for invalid in (
            "/api/rows?view=company-fundamentals&metric=ebitda",
            "/api/rows?view=company-fundamentals&metric=revenue%20OR%201=1",
            "/api/rows?view=company-fundamentals&period_end=2024-13-28",
            "/api/rows?view=company-fundamentals&cik=789019",
            "/api/rows?view=company-fundamentals&relation=sqlite_master",
        ):
            with self.subTest(invalid=invalid):
                invalid_response = self.application.handle("GET", invalid)
                self.assertEqual(invalid_response.status, 400)

        self.assertEqual(before, self._sha256(self.company))

    def test_raw_fmp_calendar_filters_lineage_and_literal_search(self) -> None:
        before = (self._sha256(self.market), self._sha256(self.macro))
        target = (
            "/api/rows?view=fmp-economic-calendar&country=us&priority=High"
            "&event_name=Non%20Farm&keyword=jobs&start_date=2026-08-07"
            "&end_date=2026-08-07&direction=asc"
        )
        response = self.application.handle("GET", target)
        self.assertEqual(response.status, 200)
        result = loads_strict(response.body)["result"]
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["query"]["mode"], "current")
        self.assertEqual(
            result["rows"][0]["capture_id"], "incremental-receipt-1"
        )
        self.assertEqual(result["rows"][0]["priority"], "High")
        self.assertEqual(result["rows"][0]["estimate"], 80)
        self.assertEqual(result["rows"][0]["actual"], -22)
        self.assertIn("<b>jobs revision</b>", result["rows"][0]["raw_row_json"])
        self.assertNotIn("response_bytes", result["rows"][0])

        response = self.application.handle("GET", target + "&mode=history")
        self.assertEqual(response.status, 200)
        history = loads_strict(response.body)["result"]
        self.assertEqual(history["total"], 3)
        self.assertEqual(history["query"]["mode"], "history")
        self.assertEqual(
            [row["capture_id"] for row in history["rows"]],
            [
                "raw-capture-1",
                "raw-capture-2",
                "incremental-receipt-1",
            ],
        )
        self.assertEqual(
            [row["actual"] for row in history["rows"]], [-23, -22, -22]
        )

        response = self.application.handle(
            "GET",
            "/api/rows?view=fmp-economic-calendar&country=CA&priority=Low",
        )
        result = loads_strict(response.body)["result"]
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["rows"][0]["event_name"], "Employment Change")

        response = self.application.handle(
            "GET",
            "/api/rows?view=fmp-economic-calendar&priority=Medium",
        )
        result = loads_strict(response.body)["result"]
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["rows"][0]["event_name"], "CPI MoM (Jul)")

        response = self.application.handle(
            "GET",
            "/api/rows?view=fmp-economic-calendar&keyword=%25",
        )
        result = loads_strict(response.body)["result"]
        self.assertEqual(result["total"], 2)
        self.assertEqual(
            {row["event_name"] for row in result["rows"]},
            {"CPI MoM (Jul)", "Unemployment Rate (Jul)"},
        )

        response = self.application.handle(
            "GET",
            "/api/rows?view=fmp-economic-calendar&keyword=_",
        )
        self.assertEqual(loads_strict(response.body)["result"]["total"], 0)

        response = self.application.handle(
            "GET",
            "/api/rows?view=fmp-economic-calendar&keyword=Hidden",
        )
        self.assertEqual(loads_strict(response.body)["result"]["total"], 0)

        response = self.application.handle(
            "GET",
            "/api/rows?view=fmp-economic-calendar&direction=asc&limit=1&page=2",
        )
        result = loads_strict(response.body)["result"]
        self.assertEqual(result["total"], 4)
        self.assertEqual(
            result["rows"][0]["capture_id"], "incremental-receipt-1"
        )
        self.assertEqual(result["rows"][0]["event_name"], "Unemployment Rate (Jul)")

        response = self.application.handle(
            "GET",
            "/api/rows?view=fmp-economic-calendar&mode=all",
        )
        self.assertEqual(response.status, 400)

        document = self.application.handle(
            "GET",
            "/?view=fmp-economic-calendar&country=US&priority=High"
            "&event_name=Non%20Farm&keyword=jobs&direction=asc",
        ).body.decode("utf-8")
        self.assertIn("Raw FMP calendar", document)
        self.assertIn('name="mode"', document)
        self.assertIn(">Current</option>", document)
        self.assertIn(">History</option>", document)
        self.assertIn('name="country"', document)
        self.assertIn('name="priority"', document)
        self.assertIn('name="event_name"', document)
        self.assertIn('name="keyword"', document)
        self.assertIn("&lt;b&gt;jobs revision&lt;/b&gt;", document)
        self.assertNotIn("<b>jobs revision</b>", document)
        self.assertEqual(before, (self._sha256(self.market), self._sha256(self.macro)))


    def test_macro_surprises_accept_employment_kinds_and_project_lineage_ids(
        self,
    ) -> None:
        before = (self._sha256(self.market), self._sha256(self.macro))
        payroll = ReleaseSurprise(
            event_id="event-payroll",
            event_version_id="event-version-payroll",
            kind=NONFARM_PAYROLLS_KIND,
            event_at="2024-06-07T12:30:00Z",
            reference_period="2024-05",
            consensus=Decimal("150"),
            consensus_mapping_basis="same_day_bls_capture",
            fmp_actual=Decimal("999"),
            official_actual=Decimal("200"),
            surprise=Decimal("50"),
            unit="thousands_persons",
            actual_source="bls_employment_vintage",
            availability_assumption="event_at_utc",
            status="ok",
            official_version_id="payroll-target-version",
            official_prior_version_id="payroll-prior-version",
        )
        unemployment = ReleaseSurprise(
            event_id="event-unemployment",
            event_version_id="event-version-unemployment",
            kind=UNEMPLOYMENT_RATE_KIND,
            event_at="2024-07-05T12:30:00Z",
            reference_period="2024-06",
            consensus=Decimal("4.0"),
            consensus_mapping_basis="first_rtdsm_vintage_proxy",
            fmp_actual=Decimal("9.9"),
            official_actual=Decimal("4.1"),
            surprise=Decimal("0.1"),
            unit="percent",
            actual_source="bls_employment_vintage",
            availability_assumption="event_at_utc",
            status="ok",
            official_version_id="unemployment-target-version",
        )
        with patch(
            "quant_data.canonical_inspector.MacroReleaseSurpriseRepository"
        ) as repository:
            repository.return_value.query.return_value = (payroll,)
            response = self.application.handle(
                "GET",
                f"/api/rows?view=macro-surprises&kind={NONFARM_PAYROLLS_KIND}",
            )
        self.assertEqual(response.status, 200)
        payload = loads_strict(response.body)
        row = payload["result"]["rows"][0]
        self.assertEqual(row["reference_period"], "2024-05")
        self.assertEqual(row["unit"], "thousands_persons")
        self.assertEqual(row["official_version_id"], "payroll-target-version")
        self.assertEqual(row["official_prior_version_id"], "payroll-prior-version")
        repository.return_value.query.assert_called_once_with(
            start_date=None,
            end_date=None,
            kinds=(NONFARM_PAYROLLS_KIND,),
        )

        with patch(
            "quant_data.canonical_inspector.MacroReleaseSurpriseRepository"
        ) as repository:
            repository.return_value.query.return_value = (unemployment,)
            html_response = self.application.handle(
                "GET",
                f"/?view=macro-surprises&kind={UNEMPLOYMENT_RATE_KIND}",
            )
        self.assertEqual(html_response.status, 200)
        document = html_response.body.decode("utf-8")
        self.assertIn(NONFARM_PAYROLLS_KIND, document)
        self.assertIn(UNEMPLOYMENT_RATE_KIND, document)
        self.assertIn("GDP growth — best available release", document)
        self.assertEqual(before, (self._sha256(self.market), self._sha256(self.macro)))

    def test_macro_surprise_projects_coalesced_fmp_cpi_mapping(self) -> None:
        before = (self._sha256(self.market), self._sha256(self.macro))
        cpi = ReleaseSurprise(
            event_id="event-cpi",
            event_version_id="event-version-cpi",
            kind=CPI_HEADLINE_MOM_KIND,
            event_at="2025-12-18T13:30:00Z",
            reference_period="2025-11",
            consensus=Decimal("0.3"),
            consensus_mapping_basis=(
                "same_day_fmp_cpi_complementary_fields"
            ),
            fmp_actual=Decimal("0.1"),
            official_actual=Decimal("0.1"),
            surprise=Decimal("-0.2"),
            unit="%",
            actual_source="fmp_calendar",
            availability_assumption="event_at_utc",
            status="ok",
            official_version_id=None,
            coalesced_event_version_ids=("event-version-cpi-actual",),
        )
        with patch(
            "quant_data.canonical_inspector.MacroReleaseSurpriseRepository"
        ) as repository:
            repository.return_value.query.return_value = (cpi,)
            response = self.application.handle(
                "GET",
                f"/api/rows?view=macro-surprises&kind={CPI_HEADLINE_MOM_KIND}",
            )
        self.assertEqual(response.status, 200)
        result = loads_strict(response.body)["result"]
        row = result["rows"][0]
        self.assertEqual(row["actual_source"], "fmp_calendar")
        self.assertEqual(row["reference_period"], "2025-11")
        self.assertEqual(
            row["mapping"],
            "same_day_fmp_cpi_complementary_fields",
        )
        self.assertEqual(row["fmp_actual"], "0.1")
        self.assertEqual(row["official_actual"], "0.1")
        self.assertEqual(row["surprise"], "-0.2")
        self.assertIsNone(row["official_version_id"])
        self.assertEqual(
            row["coalesced_event_version_ids"],
            ["event-version-cpi-actual"],
        )
        self.assertIn("fmp_actual", result["columns"])
        self.assertIn("coalesced_event_version_ids", result["columns"])
        self.assertEqual(before, (self._sha256(self.market), self._sha256(self.macro)))

    def test_current_agent_tools_show_latest_version_and_keep_compatibility_calls(self) -> None:
        before = (
            self._sha256(self.market),
            self._sha256(self.macro),
            self._sha256(self.company),
        )
        manifest_response = self.application.handle("GET", "/api/agent-tools")
        self.assertEqual(manifest_response.status, 200)
        manifest = loads_strict(manifest_response.body)
        self.assertEqual(manifest["registry_revision"], "2.74.0")
        self.assertEqual(len(manifest["tools"]), 77)
        technical = next(
            item
            for item in manifest["tools"]
            if item["name"] == "market.technical_indicators"
        )
        self.assertEqual(
            [item["version"] for item in technical["versions"]],
            [
                "1.0.0",
                "2.0.0",
                "2.1.0",
                "2.2.0",
                "2.3.0",
                "2.4.0",
                "2.5.0",
                "2.6.0",
                "2.7.0",
            ],
        )
        v20 = next(
            item for item in technical["versions"] if item["version"] == "2.0.0"
        )
        v23 = next(
            item for item in technical["versions"] if item["version"] == "2.3.0"
        )
        v27 = next(
            item for item in technical["versions"] if item["version"] == "2.7.0"
        )
        self.assertIn(
            "ema",
            v20["input_schema"]["properties"]["indicator"]["enum"],
        )
        self.assertIn(
            "kdj",
            v23["input_schema"]["properties"]["indicator"]["enum"],
        )
        self.assertIn(
            "rolling_regression_line",
            v27["input_schema"]["properties"]["indicator"]["enum"],
        )
        news = next(item for item in manifest["tools"] if item["name"] == "news.search")
        self.assertIn("2.1.0", [item["version"] for item in news["versions"]])
        data_status = next(
            item
            for item in manifest["tools"]
            if item["name"] == "data.get_dataset_status"
        )
        self.assertEqual(data_status["version"], "1.0.0")
        options_captures = next(
            item
            for item in manifest["tools"]
            if item["name"] == "options.search_captures"
        )
        self.assertEqual(
            [item["version"] for item in options_captures["versions"]],
            ["1.0.0", "2.0.0"],
        )

        page = self.application.handle(
            "GET",
            "/agent-tools?tool=market.technical_indicators",
        )
        self.assertEqual(page.status, 200)
        document = page.body.decode("utf-8")
        self.assertIn("<p>Logical tools</p><strong>77</strong>", document)
        self.assertIn('name="tool_version"', document)
        self.assertIn(
            'name="tool_version" value="2.7.0" readonly required',
            document,
        )
        self.assertNotIn('<option value="2.3.0"', document)
        self.assertIn("Latest versions", document)
        self.assertIn("One per logical tool", document)
        self.assertIn("Latest version", document)
        self.assertIn("Point-in-time policy", document)
        self.assertNotIn("Callable versions", document)
        self.assertIn("rolling_regression_line", document)
        self.assertIn("news.search", document)
        self.assertIn("Current news", document)
        self.assertIn('href="/news"', document)
        self.assertIn("Data status", document)
        self.assertIn('href="/data-status"', document)
        self.assertIn("Registered data coverage", document)
        self.assertNotIn(str(self.market), document)

        latest_link = self.application.handle(
            "GET",
            "/agent-tools?tool=market.technical_indicators"
            "&tool_version=2.7.0",
        )
        self.assertEqual(latest_link.status, 200)

        kdj_page = self.application.handle(
            "GET",
            "/agent-tools?tool=market.technical_indicators"
            "&tool_version=2.3.0",
        )
        self.assertEqual(kdj_page.status, 400)

        response = self.application.handle(
            "POST",
            "/api/agent-tools/call",
            body=dumps_strict(
                {
                    "api_version": manifest["api_version"],
                    "tool": technical["name"],
                    "tool_version": v27["version"],
                    "arguments": v27["examples"][0],
                }
            ).encode("utf-8"),
        )
        self.assertEqual(response.status, 200)
        payload = loads_strict(response.body)
        self.assertEqual(payload["receipt"]["tool_version"], "2.7.0")
        self.assertEqual(payload["result"]["status"], "ok")
        self.assertEqual(
            payload["result"]["series"][0]["metadata"]["indicator"],
            "rolling_regression_line",
        )

        kdj_arguments = copy.deepcopy(v23["examples"][0])
        base_series = kdj_arguments["series"][0]
        kdj_series = []
        for role, values in (
            ("high", ("10", "12", "14", "16", "18")),
            ("low", ("4", "5", "6", "8", "10")),
            ("close", ("7", "8.5", "9", "10.5", "12")),
        ):
            series = copy.deepcopy(base_series)
            series["series_id"] = f"inspector-kdj:{role}"
            series["metadata"]["observation_field"] = role
            series["audit"]["observation_field"] = role
            for observation, value in zip(
                series["observations"],
                values,
                strict=True,
            ):
                observation["value"] = Decimal(value)
                observation["dimensions"]["observation_field"] = role
            lineage_material = {
                key: value
                for key, value in series.items()
                if key != "lineage_digest"
            }
            series["lineage_digest"] = hashlib.sha256(
                dumps_strict(lineage_material).encode("utf-8")
            ).hexdigest()
            kdj_series.append(series)
        kdj_arguments.update(
            {
                "series": kdj_series,
                "indicator": "kdj",
                "window": 3,
                "signal_window": 2,
                "limit": 5,
            }
        )
        kdj_response = self.application.handle(
            "POST",
            "/api/agent-tools/call",
            body=dumps_strict(
                {
                    "api_version": manifest["api_version"],
                    "tool": technical["name"],
                    "tool_version": v23["version"],
                    "arguments": kdj_arguments,
                }
            ).encode("utf-8"),
        )
        self.assertEqual(
            kdj_response.status,
            200,
            kdj_response.body.decode("utf-8"),
        )
        kdj_payload = loads_strict(kdj_response.body)
        self.assertEqual(kdj_payload["receipt"]["tool_version"], "2.3.0")
        self.assertEqual(
            tuple(
                item["metadata"]["component"]
                for item in kdj_payload["result"]["series"]
            ),
            ("percent_k", "percent_d", "percent_j"),
        )
        self.assertEqual(
            before,
            (
                self._sha256(self.market),
                self._sha256(self.macro),
                self._sha256(self.company),
            ),
        )

    def test_local_agent_guide_latest_contract_table_matches_manifest(self) -> None:
        source = (PROJECT_ROOT / "docs" / "LOCAL_AGENT_TOOLS.md").read_text(
            encoding="utf-8"
        )
        documented = dict(
            re.findall(
                r"^\| `([a-z0-9_.]+)` \| `(\d+\.\d+\.\d+)` \|$",
                source,
                flags=re.MULTILINE,
            )
        )
        expected = {}
        for tool in self.application.dispatcher.manifest()["tools"]:
            variants = tool.get("versions")
            available = (
                tuple(variants)
                if isinstance(variants, list) and variants
                else (tool,)
            )
            latest = max(
                available,
                key=lambda item: tuple(
                    int(part) for part in item["version"].split(".")
                ),
            )
            expected[tool["name"]] = latest["version"]
        self.assertEqual(len(expected), 77)
        self.assertEqual(documented, expected)

    def test_current_news_v21_runs_through_inspector_without_store_mutation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            stores = StoreMap.four_explicit(
                market=root / "market.sqlite",
                macro=root / "macro.sqlite",
                company=root / "company.sqlite",
                news=root / "news.sqlite",
            )
            initialize_all(stores, self.registry)
            application = CanonicalInspectorApplication(stores, self.registry)
            paths = (
                root / "market.sqlite",
                root / "macro.sqlite",
                root / "company.sqlite",
                root / "news.sqlite",
            )
            before = tuple(self._sha256(path) for path in paths)
            manifest = loads_strict(
                application.handle("GET", "/api/agent-tools").body
            )
            news = next(
                item for item in manifest["tools"] if item["name"] == "news.search"
            )
            v21 = next(
                item for item in news["versions"] if item["version"] == "2.1.0"
            )
            response = application.handle(
                "POST",
                "/api/agent-tools/call",
                body=dumps_strict(
                    {
                        "api_version": manifest["api_version"],
                        "tool": news["name"],
                        "tool_version": v21["version"],
                        "arguments": v21["examples"][0],
                    }
                ).encode("utf-8"),
            )
            self.assertEqual(response.status, 200)
            payload = loads_strict(response.body)
            self.assertEqual(payload["receipt"]["tool_version"], "2.1.0")
            self.assertEqual(payload["result"]["status"], "ok")
            self.assertEqual(payload["result"]["records"], [])
            serialized = dumps_strict(payload)
            for private_name in ("body_text", "response_bytes", "image_url"):
                self.assertNotIn(f'"{private_name}":', serialized)
            self.assertEqual(before, tuple(self._sha256(path) for path in paths))

    def test_current_news_page_runs_v23_and_renders_bounded_headlines(
        self,
    ) -> None:
        result = {
            "records": [
                {
                    "record_type": "current_news_headline_v2_3",
                    "fields": [
                        {"name": "feed_id", "value": "alpaca_benzinga"},
                        {
                            "name": "headline",
                            "value": "Cloud capacity agreement expands",
                        },
                        {
                            "name": "summary",
                            "value": "A bounded retained summary.",
                        },
                        {
                            "name": "symbols",
                            "value": '["MSFT","NVDA"]',
                        },
                        {
                            "name": "published_normalized_at",
                            "value": "2026-09-01T02:38:07.000000Z",
                        },
                        {
                            "name": "published_date_raw",
                            "value": "2026-09-01T02:38:07Z",
                        },
                        {
                            "name": "available_at",
                            "value": "2026-09-01T03:10:06.179546Z",
                        },
                        {
                            "name": "source_url",
                            "value": "https://example.test/news/cloud",
                        },
                    ],
                }
            ],
            "truncation": {
                "applied": True,
                "has_more": True,
                "limit": 10,
                "next_cursor": "cursor-2",
                "returned_count": 1,
                "total_known_count": 42,
            },
        }
        source_status = {"records": [], "truncation": {"returned_count": 0}}
        with patch.object(
            self.application.dispatcher,
            "call",
            side_effect=(result, source_status),
        ) as dispatcher_call:
            response = self.application.handle(
                "GET",
                "/news?query=cloud&symbol=nvda"
                "&source_id=alpaca_benzinga"
                "&start_date=2026-08-01&end_date=2026-09-01&limit=10",
            )
        self.assertEqual(response.status, 200)
        self.assertIn(b'value="finviz"', response.body)
        self.assertIn(b'value="financialjuice"', response.body)
        dispatcher_call.assert_has_calls(
            [
                call(
                    "news.search",
                    {
                        "query": "cloud",
                        "symbols": ["NVDA"],
                        "source_ids": ["alpaca_benzinga"],
                        "mode": "latest",
                        "as_of": None,
                        "date_only_policy": "completed_date",
                        "start_date": "2026-08-01",
                        "end_date": "2026-09-01",
                        "cursor": None,
                        "limit": 10,
                    },
                    tool_version="2.3.0",
                ),
                call(
                    "news.get_source_status",
                    {"source_ids": []},
                    tool_version="1.0.0",
                ),
            ]
        )
        self.assertEqual(dispatcher_call.call_count, 2)
        document = response.body.decode("utf-8")
        self.assertIn("<h1>Current news</h1>", document)
        self.assertIn('href="/news" aria-current="page"', document)
        self.assertIn("Cloud capacity agreement expands", document)
        self.assertIn("A bounded retained summary.", document)
        self.assertIn("MSFT, NVDA", document)
        self.assertIn("42 retained rows match this selection.", document)
        self.assertIn("cursor=cursor-2", document)
        self.assertIn("https://example.test/news/cloud", document)
        self.assertNotIn("body_text", document)
        self.assertNotIn("response_bytes", document)
        self.assertNotIn(str(self.stores.path(StoreRole.NEWS)), document)

        with patch.object(self.application.dispatcher, "call") as invalid_call:
            rejected = self.application.handle(
                "GET",
                "/news?path=/tmp/news.sqlite",
            )
        self.assertEqual(rejected.status, 200)
        self.assertIn(
            "Current news query contains an unsupported field",
            rejected.body.decode("utf-8"),
        )
        invalid_call.assert_called_once_with(
            "news.get_source_status",
            {"source_ids": []},
            tool_version="1.0.0",
        )

    def test_data_status_routes_use_the_public_read_only_tool(self) -> None:
        result = {
            "records": [
                {
                    "record_type": "dataset_status_v1",
                    "fields": [
                        {"name": "id", "value": "macro.fed_h41_liquidity"},
                        {"name": "store", "value": "macro"},
                        {"name": "status", "value": "stale"},
                        {"name": "status_reason", "value": "threshold_exceeded"},
                        {
                            "name": "latest_successful_capture",
                            "value": '{"captured_at":"2026-08-01T00:00:00Z"}',
                        },
                        {
                            "name": "latest_retained_outcome",
                            "value": '{"outcome_kind":"success"}',
                        },
                        {
                            "name": "latest_reference_period",
                            "value": '{"period_end":"2026-08-15"}',
                        },
                        {
                            "name": "freshness",
                            "value": '{"stale_after":"P7D"}',
                        },
                    ],
                }
            ],
            "truncation": {"returned_count": 1},
        }
        arguments = {
            "stores": [],
            "dataset_ids": [],
            "statuses": [],
            "limit": 128,
        }
        with patch.object(
            self.application.dispatcher,
            "call",
            return_value=result,
        ) as dispatcher_call, patch.object(
            self.application, "_schedule_reader", return_value={
                "macro.fed_h41_liquidity": {
                    "refresh_cadence": "Weekdays at 18:30 New York",
                    "next_scheduled_fetch": "2099-01-05T23:30:00Z",
                    "schedule_state": "scheduled",
                    "schedule_note": "Scheduled batch start.",
                }
            },
        ) as schedule_reader, patch.object(
            self.application, "_metadata_reader", return_value={
                "macro.fed_h41_liquidity": {"as_of_date": "2026-08-12"}
            },
        ) as metadata_reader:
            page = self.application.handle("GET", "/data-status")
            api = self.application.handle("GET", "/api/data-status")
            self.application.handle("GET", "/healthz")
            schedule_reader.assert_called_once_with()
            metadata_reader.assert_called_once_with()
        self.assertEqual(page.status, 200)
        document = page.body.decode("utf-8")
        self.assertIn("<h1>Data status</h1>", document)
        self.assertIn("macro.fed_h41_liquidity", document)
        self.assertIn("Live data \u00b7 all supporting fields preserved", document)
        self.assertIn('data-status-group="live"', document)
        self.assertIn('data-status-group="other"', document)
        self.assertIn("read-only status", document)
        self.assertIn("2026-08-12", document)
        self.assertIn("Refresh Cadence", document)
        self.assertIn("Next Scheduled Fetch", document)
        self.assertIn("Weekdays at 18:30 New York", document)
        self.assertIn('datetime="2099-01-05T23:30:00Z"', document)
        self.assertNotIn(str(self.market), document)
        self.assertEqual(api.status, 200)
        payload = loads_strict(api.body)
        self.assertEqual(payload["execution"], "read_only")
        self.assertEqual(payload["receipt"]["route"], "data_status")
        self.assertEqual(payload["result"], result)
        self.assertEqual(
            dispatcher_call.call_args_list,
            [
                call(
                    "data.get_dataset_status",
                    arguments,
                    tool_version="1.0.0",
                ),
                call(
                    "data.get_dataset_status",
                    arguments,
                    tool_version="1.0.0",
                ),
            ],
        )

    def test_data_status_rejects_queries_without_dispatch(self) -> None:
        with patch.object(self.application.dispatcher, "call") as dispatcher_call, patch.object(
            self.application, "_schedule_reader"
        ) as schedule_reader:
            response = self.application.handle(
                "GET", "/data-status?path=/tmp/store.sqlite"
            )
        self.assertEqual(response.status, 400)
        self.assertIn("Data status does not accept query fields", response.body.decode("utf-8"))
        dispatcher_call.assert_not_called()
        schedule_reader.assert_not_called()

    def test_agent_tools_selection_and_methods_fail_closed(self) -> None:
        cases = (
            ("/agent-tools?path=/tmp/news.sqlite", 400),
            ("/agent-tools?tool=unknown", 400),
            (
                "/agent-tools?tool=market.technical_indicators"
                "&tool_version=99.0.0",
                400,
            ),
            (
                "/agent-tools?tool=market.technical_indicators"
                "&tool_version=2.3.0",
                400,
            ),
            ("/agent-tools?tool_version=2.7.0", 400),
            ("/api/agent-tools?sql=select", 400),
        )
        for target, expected in cases:
            with self.subTest(target=target):
                response = self.application.handle("GET", target)
                self.assertEqual(response.status, expected)
                self.assertNotIn(str(self.market), response.body.decode("utf-8"))
        self.assertEqual(
            self.application.handle("POST", "/agent-tools", body=b"{}").status,
            405,
        )
        self.assertEqual(
            self.application.handle("POST", "/news", body=b"{}").status,
            405,
        )
        self.assertEqual(
            self.application.handle("POST", "/data-status", body=b"{}").status,
            405,
        )
        self.assertEqual(
            self.application.handle("POST", "/api/data-status", body=b"{}").status,
            405,
        )
        self.assertEqual(
            self.application.handle("POST", "/api/agent-tools", body=b"{}").status,
            405,
        )
        self.assertEqual(
            self.application.handle("GET", "/api/agent-tools/call").status,
            404,
        )

    def test_healthz_reports_process_liveness_only(self) -> None:
        with patch.object(self.application.dispatcher, "call") as dispatcher_call:
            response = self.application.handle("GET", "/healthz")
        self.assertEqual(response.status, 200)
        dispatcher_call.assert_not_called()
        payload = loads_strict(response.body)
        self.assertEqual(payload["execution"], "read_only")
        self.assertEqual(payload["receipt"]["route"], "healthz")
        self.assertEqual(
            payload["result"],
            {
                "status": "ok",
                "service": "quant-data-inspector",
                "scope": "process_liveness_only",
                "data_freshness": "not_checked",
                "provider_health": "not_checked",
                "scheduler_health": "not_checked",
            },
        )
        self.assertEqual(
            self.application.handle("GET", "/healthz?path=/tmp/store").status,
            400,
        )
        self.assertEqual(
            self.application.handle("POST", "/healthz", body=b"{}").status,
            405,
        )

    def test_main_reports_bounded_readiness_failures(self) -> None:
        with (
            patch(
                "quant_data.canonical_inspector.build_canonical_inspector",
                side_effect=StoreUnavailableError("Canonical market store is unavailable"),
            ),
            patch("sys.stderr", new_callable=io.StringIO) as stderr,
        ):
            self.assertEqual(
                inspector_main(["--project-root", str(PROJECT_ROOT), "--port", "0"]),
                69,
            )
        self.assertEqual(
            stderr.getvalue().strip(),
            "Canonical Data Inspector unavailable: Canonical market store is unavailable",
        )

        with (
            patch(
                "quant_data.canonical_inspector.build_canonical_inspector",
                return_value=self.application,
            ),
            patch(
                "quant_data.canonical_inspector.create_server",
                side_effect=OSError("address already in use"),
            ),
            patch("sys.stderr", new_callable=io.StringIO) as stderr,
        ):
            self.assertEqual(
                inspector_main(["--project-root", str(PROJECT_ROOT), "--port", "8765"]),
                69,
            )
        self.assertEqual(
            stderr.getvalue().strip(),
            "Canonical Data Inspector unavailable: the requested loopback port cannot be opened",
        )

    def test_builder_requires_all_four_canonical_store_bindings(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            data = root / "data"
            data.mkdir()
            market = data / "market.sqlite"
            macro = data / "macro.sqlite"
            company = data / "company.sqlite"
            news = data / "news.sqlite"
            for path in (market, macro, company):
                path.touch()
            stores = StoreMap.four_explicit(
                market=market,
                macro=macro,
                company=company,
                news=news,
            )
            with (
                patch(
                    "quant_data.canonical_inspector.load_registry",
                    return_value=self.registry,
                ),
                patch(
                    "quant_data.canonical_inspector.resolve_store_map",
                    return_value=stores,
                ),
            ):
                with self.assertRaisesRegex(
                    StoreUnavailableError,
                    "Canonical news store is unavailable",
                ):
                    build_canonical_inspector(root)
                news.touch()
                application = build_canonical_inspector(root)
                self.assertIsInstance(application, CanonicalInspectorApplication)

    def test_html_assets_and_local_server_security_headers(self) -> None:
        response = self.application.handle("GET", "/?view=market-prices&symbol=AAPL")
        self.assertEqual(response.status, 200)
        document = response.body.decode("utf-8")
        self.assertIn("Canonical Data Inspector", document)
        self.assertIn("Apple Inc.", document)
        self.assertIn("Treasury curve", document)
        self.assertIn("Repo facilities", document)
        self.assertIn("Fed H.4.1 liquidity", document)
        self.assertIn("Treasury cash balance", document)
        self.assertIn("Financial conditions", document)
        self.assertIn("BIS credit conditions", document)
        self.assertIn("Corporate bond distress", document)
        self.assertIn("Natural gas storage", document)
        self.assertIn("Recession chronology", document)
        self.assertIn("Raw FMP calendar", document)
        self.assertIn("Current news", document)
        self.assertIn("Agent Tools", document)
        self.assertIn("Read-only</span>", document)
        self.assertIn("no database mutation", document)
        self.assertNotIn(str(self.market), document)
        css = self.application.handle("GET", "/assets/dashboard.css")
        self.assertEqual(css.status, 200)
        self.assertIn(b"flex-wrap: wrap", css.body)
        self.assertEqual(self.application.handle("GET", "/assets/inter-variable.woff2").status, 200)
        self.assertEqual(self.application.handle("GET", "/assets/inspector-tools.css").status, 200)
        self.assertEqual(self.application.handle("GET", "/assets/inspector-tools.js").status, 200)
        for asset, content_type in (
            ("/assets/inspector.css", "text/css; charset=utf-8"),
            ("/assets/inspector.js", "application/javascript; charset=utf-8"),
        ):
            asset_response = self.application.handle("GET", asset)
            self.assertEqual(asset_response.status, 200)
            self.assertEqual(asset_response.content_type, content_type)
            self.assertIn(asset, document)
            self.assertEqual(self.application.handle("GET", asset + "?path=/tmp/other").status, 400)

        server = create_server(self.application, host="127.0.0.1", port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            client.request("GET", "/?view=macro-current")
            served = client.getresponse()
            served.read()
            self.assertEqual(served.status, 200)
            self.assertEqual(served.getheader("Cache-Control"), "no-store")
            self.assertEqual(served.getheader("X-Frame-Options"), "DENY")
            self.assertIn("default-src 'self'", served.getheader("Content-Security-Policy"))
            client.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        with self.assertRaises(ValidationError):
            create_server(self.application, host="0.0.0.0", port=0)

    def test_unknown_controls_methods_and_bounds_fail_closed(self) -> None:
        cases = (
            ("/api/rows?view=market-prices&sql=select", 400),
            ("/api/rows?view=market-prices&path=/tmp/store", 400),
            ("/api/rows?view=market-instruments&relation=sqlite_master", 400),
            ("/api/rows?view=market-prices&symbol=AAPL%20OR%201=1", 400),
            ("/api/rows?view=macro-current&series=unknown", 400),
            ("/api/rows?view=macro-surprises&stage=revised", 400),
            ("/api/rows?view=treasury-curve&tenor=overnight", 400),
            ("/api/rows?view=repo-facilities&facility=SRP", 400),
            ("/api/rows?view=soma-summary&component=cusip", 400),
            (
                f"/api/rows?view=macro-surprises&kind={NONFARM_PAYROLLS_KIND}"
                "&stage=second",
                400,
            ),
            ("/api/rows?view=fmp-economic-calendar&priority=Critical", 400),
            (
                "/api/rows?view=fmp-economic-calendar&start_date=2026-08-08"
                "&end_date=2026-08-07",
                400,
            ),
            ("/api/rows?view=fmp-economic-calendar&relation=sqlite_master", 400),
            ("/api/rows?view=market-prices&page=1001", 413),
        )
        for target, expected in cases:
            with self.subTest(target=target):
                response = self.application.handle("GET", target)
                self.assertEqual(response.status, expected)
                payload = loads_strict(response.body)
                self.assertIn(payload["error"]["code"], {"invalid_request", "resource_limit"})
                self.assertNotIn(str(self.market), response.body.decode("utf-8"))

        self.assertEqual(self.application.handle("POST", "/", body=b"{}").status, 405)
        self.assertEqual(self.application.handle("DELETE", "/").status, 405)
        self.assertEqual(self.application.handle("GET", "/missing").status, 404)


if __name__ == "__main__":
    unittest.main()
