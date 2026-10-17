# V2 successor and investment-analysis actual-data audit — 2026-08-28

## Status and scope

This observed checkpoint ran 31 pre-2.57 successor variants and 15 new 2.57 investment-analysis variants against registry 2.57.0. Every call was discovered from the public manifest and invoked separately through bin/quant-data-tools call.

The runner never imports a store, selects a database path, opens SQLite, sends a provider request, or supplies a credential. Store-backed calls use the host-owned quiet immutable reader, which validates its held descriptor and sidecars before and after the read. The report retains only concise public, path-free receipt fields.

AAPL/MSFT inputs are actual non-truncated Stage 10 trailing simple returns from 2026-07-01 through 2026-08-12. Technical inputs are actual AAPL OHLCV. Composition uses ordinary Python json.loads and json.dumps to exercise the external-agent round-trip path.

Research routes with no genuine retained event, signal, forecast, model design, or p-value input are explicitly marked contract-only. Their results prove interface behavior, not economic validity.

## Pre-2.57 successor variants

| Observed outcome | Tools |
| --- | ---: |
| invalid_request | 1 |
| not_established | 3 |
| useful_or_derived_output | 27 |

| Tool | Version | Input basis | Outcome | Concise result | Notes |
| --- | --- | --- | --- | --- | --- |
| macro.search_series | 2.0.0 | canonical macro store | useful_or_derived_output | status:ok; records:6; series:0 | Retained macro identifier discovery. |
| macro.describe_series | 2.0.0 | canonical macro store | useful_or_derived_output | status:ok; records:1; series:0 | Describe the public-discovered GDP identifier. |
| macro.get_series | 2.0.0 | canonical macro store | useful_or_derived_output | status:ok; records:0; series:1; observations:98 | Latest retained GDP series. |
| market.get_returns | 2.0.0 | canonical market store | useful_or_derived_output | status:ok; records:0; series:1; observations:30 | Actual AAPL rows in the populated window. |
| market.get_forward_returns | 2.0.0 | canonical market store | useful_or_derived_output | status:ok; records:0; series:1; observations:30 | Actual AAPL rows; forward values remain outcome labels. |
| market.technical_indicators | 2.0.0 | actual AAPL OHLCV | useful_or_derived_output | status:ok; records:1; series:1; observations:30 | Accumulation/distribution uses high, low, close, and volume. |
| market.technical_indicators | 2.1.0 | actual AAPL OHLCV | useful_or_derived_output | status:ok; records:5; series:5; observations:30,30,30,30,30 | Bounded SuperTrend AI over retained bars. |
| market.technical_indicators | 2.2.0 | actual AAPL OHLCV | useful_or_derived_output | status:ok; records:10; series:10; observations:30,30,30,30,30,30,30,30,30,30 | Causal swing-structure calculation over retained bars. |
| timeseries.describe | 2.0.0 | actual AAPL/MSFT trailing simple returns | useful_or_derived_output | status:ok; records:1; series:0 | Ordinary JSON composition from the AAPL CLI result. |
| timeseries.align | 2.0.0 | actual AAPL/MSFT trailing simple returns | useful_or_derived_output | status:ok; records:30; series:0 | Ordinary JSON composition from AAPL and MSFT CLI results. |
| timeseries.correlation | 2.0.0 | actual AAPL/MSFT trailing simple returns | useful_or_derived_output | status:ok; records:1; series:0 | Joint-complete AAPL/MSFT retained sample. |
| econometrics.regression | 2.0.0 | actual AAPL/MSFT trailing simple returns | useful_or_derived_output | status:ok; records:2; series:0 | Classical OLS over actual retained returns. |
| econometrics.regression | 2.1.0 | actual AAPL/MSFT trailing simple returns | useful_or_derived_output | status:ok; records:2; series:0 | HC1 OLS with fixed residual diagnostics. |
| econometrics.regression | 3.0.0 | actual AAPL/MSFT trailing simple returns | useful_or_derived_output | status:ok; records:2; series:0 | Fixed-lag VAR, not a forecasting claim. |
| econometrics.rolling_regression | 2.0.0 | actual AAPL/MSFT trailing simple returns | useful_or_derived_output | status:ok; records:23; series:0 | Fixed eight-observation rolling classical OLS. |
| econometrics.rolling_regression | 2.1.0 | actual AAPL/MSFT trailing simple returns | useful_or_derived_output | status:ok; records:23; series:0 | Fixed eight-observation rolling HC1 OLS. |
| econometrics.stationarity | 2.0.0 | actual AAPL/MSFT trailing simple returns | not_established | status:not_established; records:1; series:0 | Fixed-lag ADF over actual AAPL returns. |
| econometrics.stationarity | 2.1.0 | actual AAPL/MSFT trailing simple returns | not_established | status:not_established; records:3; series:0 | Fixed-lag ADF and level-KPSS over actual AAPL returns. |
| econometrics.structural_breaks | 2.0.0 | actual AAPL/MSFT trailing simple returns | not_established | status:not_established; records:4; series:0 | One declared break at row 12; no automatic break search. |
| data.quality_audit | 2.0.0 | actual AAPL/MSFT trailing simple returns | useful_or_derived_output | status:ok; records:16; series:0 | Coverage and lineage diagnostics over real return feeds. |
| timeseries.transform | 2.0.0 | actual AAPL/MSFT trailing simple returns | useful_or_derived_output | status:ok; records:30; series:0 | Five-observation rolling mean over actual AAPL returns. |
| research.point_in_time_panel | 2.0.0 | actual AAPL/MSFT trailing simple returns | useful_or_derived_output | status:ok; records:30; series:0 | Actual-return alignment; no invented availability evidence. |
| research.event_study | 2.0.0 | actual returns plus contract-only research role | useful_or_derived_output | status:ok; records:1; series:0 | Contract-only: event_index=1 is not a retained real event. |
| alpha.signal_diagnostics | 2.0.0 | actual returns plus contract-only research role | useful_or_derived_output | status:ok; records:3; series:0 | Contract-only: two returns are not an alpha signal/outcome pair. |
| research.walk_forward_backtest | 2.0.0 | actual returns plus contract-only research role | useful_or_derived_output | status:ok; records:1; series:0 | Contract-only: returns are not a prediction stream or strategy. |
| research.robustness_suite | 2.0.0 | actual returns plus contract-only research role | useful_or_derived_output | status:ok; records:2; series:0 | Contract-only: no model variants or robustness design exist. |
| stats.multiple_testing | 2.0.0 | actual returns plus contract-only research role | invalid_request | error:invalid_request | Contract-only: AAPL returns are deliberately not relabelled as p-values. |
| forecast.evaluate | 2.0.0 | actual returns plus contract-only research role | useful_or_derived_output | status:ok; records:1; series:0 | Contract-only: AAPL/MSFT returns are not forecast pairs. |
| company.search_filings | 2.0.0 | canonical company store | useful_or_derived_output | status:ok; records:100; series:0; truncated | First bounded AAPL filing page. |
| company.get_share_count_history | 2.0.0 | canonical company store | useful_or_derived_output | status:ok; records:217; series:0 | Reviewed AAPL share-count history. |
| market.search_instruments | 2.0.0 | canonical market store | useful_or_derived_output | status:ok; records:1; series:0 | Literal AAPL retained-identity search. |

## New 2.57 investment-analysis variants

| Observed outcome | Tools |
| --- | ---: |
| useful_or_derived_output | 15 |

| Tool | Version | Input basis | Outcome | Concise result | Notes |
| --- | --- | --- | --- | --- | --- |
| macro.get_release_calendar | 2.0.0 | canonical macro store | useful_or_derived_output | status:ok; records:100; series:0; truncated | First bounded retained calendar page; cursor continuation is not required here. |
| market.cross_sectional_performance | 2.0.0 | actual AAPL/MSFT prices | useful_or_derived_output | status:ok; records:2; series:0 | Explicit two-symbol endpoint-return comparison; no portfolio semantics. |
| macro.revision_analysis | 2.0.0 | canonical macro store | useful_or_derived_output | status:ok; records:98; series:0 | Retained official GDP vintages only; no unreconciled generic fallback. |
| macro.standardize_surprises | 2.0.0 | canonical macro store | useful_or_derived_output | status:ok; records:211; series:0 | Retained U.S. CPI headline month-over-month surprises. |
| rates.get_funding_conditions | 2.0.0 | canonical macro store | useful_or_derived_output | status:ok; records:9; series:0 | Raw EFFR and SOFR comparison; no policy inference. |
| rates.get_repo_facility_usage | 2.0.0 | canonical macro store | useful_or_derived_output | status:ok; records:2; series:0 | Observed ON RRP and SRF usage only. |
| rates.curve_analytics | 2.0.0 | canonical macro store | useful_or_derived_output | status:ok; records:13; series:0 | Raw 10Y minus 2Y retained Treasury curve values. |
| macro.get_liquidity_snapshot | 2.0.0 | canonical macro store | useful_or_derived_output | status:ok; records:4; series:0 | Observed liquidity components, with no composite score. |
| macro.get_liquidity_impulse | 2.0.0 | canonical macro store | useful_or_derived_output | status:ok; records:4; series:0 | Bounded 2026 retained-period liquidity deltas. |
| macro.get_credit_conditions | 2.0.0 | canonical macro store | useful_or_derived_output | status:ok; records:8; series:0 | Observed Chicago Fed, BIS, and CMDI components, no score. |
| macro.regime_snapshot | 2.0.0 | canonical macro store | useful_or_derived_output | status:ok; records:1; series:0 | Direct NBER recession-state observation, no inferred regime. |
| research.liquidity_credit_state | 2.0.0 | canonical macro store | useful_or_derived_output | status:ok; records:12; series:0 | Descriptive liquidity/credit state; no score or classification. |
| energy.get_electricity_retail_sales | 2.0.0 | canonical macro store | useful_or_derived_output | status:ok; records:100; series:0; truncated | Retained monthly electricity sales facts; no demand forecast. |
| energy.get_weekly_fundamentals | 2.0.0 | canonical macro store | useful_or_derived_output | status:ok; records:100; series:0; truncated | Retained weekly EIA U.S. petroleum-stock observations only. |
| company.get_fundamentals | 2.0.0 | canonical company store | useful_or_derived_output | status:ok; records:682; series:0 | Normalized AAPL SEC fundamentals, preserving retained metric semantics. |

## Supplemental native statistics

These four native v1 statistics are outside both versioned-variant counts. They are direct external-agent composition checks over the same actual returns.

| Tool | Outcome | Concise result |
| --- | --- | --- |
| stats.distribution_diagnostics | useful_or_derived_output | status:ok; records:8; series:0 |
| stats.bootstrap_confidence_interval | useful_or_derived_output | status:ok; records:1; series:0 |
| stats.covariance_matrix | useful_or_derived_output | status:ok; records:2; series:0 |
| stats.principal_components | useful_or_derived_output | status:ok; records:4; series:0 |

## Interpretation limits

- A successful call establishes observed public-contract behavior over retained local data only.
- Empty results describe current coverage, not completeness of the underlying source.
- Contract-only research results are not signals, forecasts, event studies, p-values, or backtests.
- A returned error is retained as a route-specific finding; this audit does not patch or retry it.

## Registry 2.59 fast-track closure

The later 2.59 fast-track reran the three previously unestablished
econometrics variants and called the four requested data-backed v2.1 analytics
against populated local stores. All seven returned `status:ok` through the
public dispatcher.

| Tool | Version | Populated-data result |
| --- | --- | --- |
| econometrics.stationarity | 2.0.0 | ADF established after reporting one expected leading return exclusion. |
| econometrics.stationarity | 2.1.0 | ADF, KPSS, and joint interpretation established with the same explicit exclusion. |
| econometrics.structural_breaks | 2.0.0 | Declared Chow break established while preserving public break index 12 and reporting one exclusion. |
| market.cross_sectional_performance | 2.1.0 | AAPL/MSFT breadth, realized volatility, relative return, and 20-observation rolling beta computed for a bounded non-truncated window. |
| energy.get_electricity_retail_sales | 2.1.0 | 100 retained rows; 88 exact 12-observation comparisons and 12 explicit unavailable leading comparisons. |
| energy.get_weekly_fundamentals | 2.1.0 | 100 retained rows; 48 exact 52-observation comparisons and 52 explicit unavailable leading comparisons. |
| company.get_fundamentals | 2.1.0 | 104 AAPL ratios: 68 liabilities-to-assets and 36 net-margin observations. |

This closure does not revise the historical 2.57 observations above. It records
the corrected adapter behavior and the later additive analytics separately.
