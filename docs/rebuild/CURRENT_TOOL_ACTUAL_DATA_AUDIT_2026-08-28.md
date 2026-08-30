# Current public-tool actual-data audit — 2026-08-28

## Status and scope

This checkpoint called all 119 public routes advertised by registry 2.61.0: 65 logical defaults and 54 explicit successors. Every route was invoked separately through bin/quant-data-tools call.

The runner never selects a database path, opens SQLite, sends a provider request, or supplies a credential. Store-backed calls use the host-owned quiet immutable reader. AAPL/MSFT statistical inputs are actual non-truncated Stage 10 returns, and technical inputs are actual AAPL OHLCV.

stats.multiple_testing@2.0.0 receives four genuine p-values produced first by actual HC1 OLS and bidirectional Granger calls. Its Benjamini-Hochberg output is therefore statistically exercised, although its current public input envelope is still shaped like a return series.

## Overall outcomes

| Observed outcome | Routes |
| --- | ---: |
| capability_unavailable | 1 |
| invalid_request | 16 |
| not_established | 30 |
| resource_limit | 1 |
| successful_empty | 6 |
| useful_or_derived_output | 65 |

## Logical defaults (v1 behavior)

| Observed outcome | Tools |
| --- | ---: |
| capability_unavailable | 1 |
| invalid_request | 16 |
| not_established | 30 |
| resource_limit | 1 |
| successful_empty | 6 |
| useful_or_derived_output | 11 |

| Tool | Version | Input basis | Outcome | Concise result | Notes |
| --- | --- | --- | --- | --- | --- |
| macro.search_series | 1.0.0 | canonical macro store | not_established | status:not_established; records:0; series:0 | Frozen default behavior on its public input. |
| macro.describe_series | 1.0.0 | canonical macro store | not_established | status:not_established; records:0; series:0 | Frozen default behavior on its public input. |
| macro.get_series | 1.0.0 | canonical macro store | invalid_request | error:invalid_request | Frozen default behavior on its public input. |
| macro.get_release_calendar | 1.0.0 | canonical macro store | useful_or_derived_output | status:ok; records:1000; series:0; truncated | First bounded retained release-calendar selection. |
| macro.get_intraday_releases | 1.0.0 | canonical macro store | capability_unavailable | error:capability_unavailable | Frozen default behavior on its public input. |
| macro.release_surprises | 1.0.0 | canonical macro store | useful_or_derived_output | status:ok; records:55; series:0 | Retained GDP release-surprise selection. |
| macro.revision_analysis | 1.0.0 | canonical macro store | not_established | status:not_established; records:0; series:0 | Frozen default behavior on its public input. |
| macro.align_us_recessions | 1.0.0 | canonical macro store | successful_empty | status:ok; records:0; series:0 | Frozen default behavior on its public input. |
| macro.standardize_surprises | 1.0.0 | canonical macro store | not_established | status:not_established; records:0; series:0 | Frozen default behavior on its public input. |
| macro.get_liquidity_snapshot | 1.0.0 | canonical macro store | not_established | status:not_established; records:0; series:0 | Frozen default behavior on its public input. |
| macro.get_liquidity_impulse | 1.0.0 | canonical macro store | not_established | status:not_established; records:0; series:0 | Frozen default behavior on its public input. |
| macro.get_credit_conditions | 1.0.0 | canonical macro store | not_established | status:not_established; records:0; series:0 | Frozen default behavior on its public input. |
| macro.regime_snapshot | 1.0.0 | canonical macro store | not_established | status:not_established; records:0; series:0 | Frozen default behavior on its public input. |
| timeseries.transform | 1.0.0 | actual Stage 10 returns | invalid_request | error:invalid_request | Frozen v1 composability check. |
| timeseries.describe | 1.0.0 | actual Stage 10 returns | invalid_request | error:invalid_request | Frozen v1 composability check. |
| timeseries.align | 1.0.0 | actual AAPL/MSFT returns | invalid_request | error:invalid_request | Frozen v1 composability check. |
| timeseries.correlation | 1.0.0 | actual AAPL/MSFT returns | invalid_request | error:invalid_request | Frozen v1 composability check. |
| econometrics.regression | 1.0.0 | actual AAPL/MSFT returns | invalid_request | error:invalid_request | Frozen v1 composability check. |
| econometrics.stationarity | 1.0.0 | actual Stage 10 returns | invalid_request | error:invalid_request | Frozen v1 composability check. |
| econometrics.rolling_regression | 1.0.0 | actual AAPL/MSFT returns | invalid_request | error:invalid_request | Frozen v1 composability check. |
| econometrics.structural_breaks | 1.0.0 | public compatibility input | not_established | status:not_established; records:1; series:0 | Frozen default behavior. |
| econometrics.local_projection | 1.0.0 | public compatibility input | not_established | status:not_established; records:1; series:0 | Frozen default behavior. |
| company.search_issuers | 1.0.0 | canonical company store | useful_or_derived_output | status:ok; records:1; series:0 | Actual AAPL issuer lookup. |
| company.search_filings | 1.0.0 | canonical company store | resource_limit | error:resource_limit | Maximum v1 AAPL filing selection. |
| company.get_fundamentals | 1.0.0 | canonical company store | useful_or_derived_output | status:ok; records:682; series:0 | Actual AAPL company selection. |
| company.get_corporate_actions | 1.0.0 | canonical company store | successful_empty | status:ok; records:0; series:0 | Actual AAPL company selection. |
| company.get_share_count_history | 1.0.0 | canonical company store | not_established | status:not_established; records:0; series:0 | Actual AAPL company selection. |
| company.get_earnings_calendar | 1.0.0 | canonical company store | successful_empty | status:ok; records:0; series:0 | Actual AAPL company selection. |
| company.get_consensus_history | 1.0.0 | canonical company store | successful_empty | status:ok; records:0; series:0 | Actual AAPL company selection. |
| company.get_guidance_history | 1.0.0 | canonical company store | successful_empty | status:ok; records:0; series:0 | Actual AAPL company selection. |
| company.get_estimate_revisions | 1.0.0 | canonical company store | not_established | status:not_established; records:0; series:0 | Actual AAPL company selection. |
| company.get_earnings_setup | 1.0.0 | canonical company store | not_established | status:not_established; records:0; series:0 | Actual AAPL company selection. |
| energy.get_electricity_retail_sales | 1.0.0 | canonical macro store | not_established | status:not_established; records:0; series:0 | Frozen default behavior on its public input. |
| energy.get_weekly_fundamentals | 1.0.0 | canonical macro store | not_established | status:not_established; records:0; series:0 | Frozen default behavior on its public input. |
| market.search_instruments | 1.0.0 | canonical market store | not_established | status:not_established; records:0; series:0 | Frozen default behavior on its public input. |
| market.get_available_ticker | 1.0.0 | canonical market store | useful_or_derived_output | status:ok; records:622; series:0 | Current retained ticker discovery. |
| market.get_price_series | 1.0.0 | canonical market store | useful_or_derived_output | status:ok; records:0; series:4; observations:30,30,30,30 | Bounded actual AAPL rows. |
| market.get_volume_series | 1.0.0 | canonical market store | useful_or_derived_output | status:ok; records:0; series:1; observations:30 | Bounded actual AAPL rows. |
| market.get_returns | 1.0.0 | canonical market store | not_established | status:not_established; records:0; series:0 | Frozen default behavior on its public input. |
| market.get_forward_returns | 1.0.0 | canonical market store | not_established | status:not_established; records:0; series:0 | Frozen default behavior on its public input. |
| market.technical_indicators | 1.0.0 | canonical market store | not_established | status:not_established; records:0; series:0 | Frozen default behavior on its public input. |
| market.cross_sectional_performance | 1.0.0 | canonical market store | not_established | status:not_established; records:0; series:0 | Frozen default behavior on its public input. |
| rates.get_funding_conditions | 1.0.0 | canonical macro store | not_established | status:not_established; records:0; series:0 | Frozen default behavior on its public input. |
| rates.get_repo_facility_usage | 1.0.0 | canonical macro store | not_established | status:not_established; records:0; series:0 | Frozen default behavior on its public input. |
| rates.curve_analytics | 1.0.0 | canonical macro store | not_established | status:not_established; records:0; series:0 | Frozen default behavior on its public input. |
| options.search_captures | 1.0.0 | canonical options store | not_established | status:not_established; records:0; series:0 | Actual AAPL options selection. |
| options.search_contracts | 1.0.0 | canonical options store | not_established | status:not_established; records:0; series:0 | Actual AAPL options selection. |
| options.get_surface_snapshot | 1.0.0 | canonical options store | not_established | status:not_established; records:0; series:0 | Actual AAPL options selection. |
| options.surface_diagnostics | 1.0.0 | canonical options store | not_established | status:not_established; records:0; series:0 | Actual AAPL options selection. |
| options.screen_contracts | 1.0.0 | canonical options store | not_established | status:not_established; records:0; series:0 | Actual AAPL options selection. |
| options.strategy_scenario | 1.0.0 | canonical options store | not_established | status:not_established; records:0; series:0 | Actual AAPL options selection. |
| research.point_in_time_panel | 1.0.0 | actual AAPL/MSFT returns | invalid_request | error:invalid_request | Frozen v1 composability check. |
| data.quality_audit | 1.0.0 | actual Stage 10 returns | invalid_request | error:invalid_request | Frozen v1 composability check. |
| research.event_study | 1.0.0 | actual Stage 10 returns | invalid_request | error:invalid_request | Frozen v1 composability check. |
| alpha.signal_diagnostics | 1.0.0 | actual AAPL/MSFT returns | invalid_request | error:invalid_request | Frozen v1 composability check. |
| research.walk_forward_backtest | 1.0.0 | actual AAPL/MSFT returns | invalid_request | error:invalid_request | Frozen v1 composability check. |
| research.robustness_suite | 1.0.0 | actual Stage 10 returns | invalid_request | error:invalid_request | Frozen v1 composability check. |
| stats.distribution_diagnostics | 1.0.0 | actual AAPL/MSFT returns | useful_or_derived_output | status:ok; records:8; series:0 | Native current-data statistic. |
| stats.covariance_matrix | 1.0.0 | actual AAPL/MSFT returns | useful_or_derived_output | status:ok; records:2; series:0 | Native current-data statistic. |
| stats.bootstrap_confidence_interval | 1.0.0 | actual AAPL/MSFT returns | useful_or_derived_output | status:ok; records:1; series:0 | Native current-data statistic. |
| stats.principal_components | 1.0.0 | actual AAPL/MSFT returns | useful_or_derived_output | status:ok; records:4; series:0 | Native current-data statistic. |
| stats.multiple_testing | 1.0.0 | actual Stage 10 returns | invalid_request | error:invalid_request | Frozen v1 composability check. |
| forecast.evaluate | 1.0.0 | actual AAPL/MSFT returns | invalid_request | error:invalid_request | Frozen v1 composability check. |
| news.search | 1.0.0 | canonical news store | successful_empty | status:ok; records:0; series:0 | Actual Apple news selection. |
| research.liquidity_credit_state | 1.0.0 | public compatibility input | not_established | status:not_established; records:0; series:0 | Frozen default behavior. |

## Pre-2.57 successors

| Observed outcome | Tools |
| --- | ---: |
| useful_or_derived_output | 31 |

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
| econometrics.stationarity | 2.0.0 | actual AAPL/MSFT trailing simple returns | useful_or_derived_output | status:ok; records:1; series:0 | Fixed-lag ADF over actual AAPL returns. |
| econometrics.stationarity | 2.1.0 | actual AAPL/MSFT trailing simple returns | useful_or_derived_output | status:ok; records:3; series:0 | Fixed-lag ADF and level-KPSS over actual AAPL returns. |
| econometrics.structural_breaks | 2.0.0 | actual AAPL/MSFT trailing simple returns | useful_or_derived_output | status:ok; records:4; series:0 | One declared break at row 12; no automatic break search. |
| data.quality_audit | 2.0.0 | actual AAPL/MSFT trailing simple returns | useful_or_derived_output | status:ok; records:16; series:0 | Coverage and lineage diagnostics over real return feeds. |
| timeseries.transform | 2.0.0 | actual AAPL/MSFT trailing simple returns | useful_or_derived_output | status:ok; records:30; series:0 | Five-observation rolling mean over actual AAPL returns. |
| research.point_in_time_panel | 2.0.0 | actual AAPL/MSFT trailing simple returns | useful_or_derived_output | status:ok; records:30; series:0 | Actual-return alignment; no invented availability evidence. |
| research.event_study | 2.0.0 | actual returns plus contract-only research role | useful_or_derived_output | status:ok; records:1; series:0 | Contract-only: event_index=1 is not a retained real event. |
| alpha.signal_diagnostics | 2.0.0 | actual returns plus contract-only research role | useful_or_derived_output | status:ok; records:3; series:0 | Contract-only: two returns are not an alpha signal/outcome pair. |
| research.walk_forward_backtest | 2.0.0 | actual returns plus contract-only research role | useful_or_derived_output | status:ok; records:1; series:0 | Contract-only: returns are not a prediction stream or strategy. |
| research.robustness_suite | 2.0.0 | actual returns plus contract-only research role | useful_or_derived_output | status:ok; records:2; series:0 | Contract-only: no model variants or robustness design exist. |
| stats.multiple_testing | 2.0.0 | genuine econometric p-values | useful_or_derived_output | status:ok; records:5; series:0 | Four p-values from actual HC1 OLS and bidirectional Granger tests. |
| forecast.evaluate | 2.0.0 | actual returns plus contract-only research role | useful_or_derived_output | status:ok; records:1; series:0 | Contract-only: AAPL/MSFT returns are not forecast pairs. |
| company.search_filings | 2.0.0 | canonical company store | useful_or_derived_output | status:ok; records:100; series:0; truncated | First bounded AAPL filing page. |
| company.get_share_count_history | 2.0.0 | canonical company store | useful_or_derived_output | status:ok; records:217; series:0 | Reviewed AAPL share-count history. |
| market.search_instruments | 2.0.0 | canonical market store | useful_or_derived_output | status:ok; records:1; series:0 | Literal AAPL retained-identity search. |

## Registry 2.57 investment-analysis successors

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

## Post-2.57 successors

| Observed outcome | Tools |
| --- | ---: |
| useful_or_derived_output | 8 |

| Tool | Version | Input basis | Outcome | Concise result | Notes |
| --- | --- | --- | --- | --- | --- |
| market.technical_indicators | 2.3.0 | actual AAPL OHLCV | useful_or_derived_output | status:ok; records:3; series:3; observations:30,30,30 | KDJ over the retained daily bar grid. |
| market.technical_indicators | 2.4.0 | actual AAPL OHLCV | useful_or_derived_output | status:ok; records:4; series:4; observations:30,30,30,30 | Williams Vix Fix over retained closes. |
| market.technical_indicators | 2.5.0 | actual AAPL OHLCV | useful_or_derived_output | status:ok; records:4; series:4; observations:30,30,30,30 | WaveTrend with crosses over retained HLC bars. |
| market.technical_indicators | 2.6.0 | actual AAPL OHLCV | useful_or_derived_output | status:ok; records:1; series:1; observations:30 | Parabolic SAR over retained HLC bars. |
| market.cross_sectional_performance | 2.1.0 | actual AAPL/MSFT prices | useful_or_derived_output | status:ok; records:2; series:0 | Breadth, realized volatility, relative return, and rolling beta. |
| energy.get_electricity_retail_sales | 2.1.0 | canonical macro store | useful_or_derived_output | status:ok; records:100; series:0; truncated | Exact 12-observation seasonal comparison. |
| energy.get_weekly_fundamentals | 2.1.0 | canonical macro store | useful_or_derived_output | status:ok; records:100; series:0; truncated | Exact 52-observation seasonal comparison. |
| company.get_fundamentals | 2.1.0 | canonical company store | useful_or_derived_output | status:ok; records:104; series:0 | Reviewed same-period AAPL net-margin and leverage ratios. |

## Interpretation limits

- A successful call establishes observed public-contract behavior over retained local data only.
- Empty results describe current coverage, not completeness of the underlying source.
- Contract-only event, signal, forecast, and backtest calls do not establish economic validity.
- Frozen v1 incompatibilities are retained for compatibility; their working successors are audited separately.
- A returned error is retained as a route-specific finding; this audit does not hide or retry it.
