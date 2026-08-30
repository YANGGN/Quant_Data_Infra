# V1 tool actual-data audit — 2026-08-27

## Status and scope

This is an observed-result checkpoint for the 65 public tool names advertised by
system registry `2.50.0`. Each tool was invoked separately through the fixed
local `bin/quant-data-tools call` interface with `tool_version` pinned to
`1.0.0`. No database path, SQL, provider request, credential, network access,
or direct SQLite connection was supplied by the caller.

Canonical market, macro, company, options, and news reads used the shared quiet
immutable gateway. It pins a read-only descriptor, requires quiet WAL and
rollback-journal state, opens SQLite through `/proc/self/fd` with
`mode=ro&immutable=1`, verifies `query_only`, validates the store role and
anchor, and checks main-file and sidecar identity before and after the read.

The populated-data analysis source was a non-truncated pair of Stage 10
one-observed-row trailing simple-return series for AAPL and MSFT, from
2026-07-01 through 2026-08-12. Each contained 30 observations and was obtained
through `market.get_returns@2.0.0` only as an input feeder for the four native
v1 statistics tools.

## Outcome matrix

The classifications below are mutually exclusive and cover all 65 tools.

| Observed outcome | Count | Interpretation |
| --- | ---: | --- |
| Non-empty useful result | 11 | The tool ran against retained canonical data and returned usable records, series, or matrices. |
| Successful empty selection | 6 | The read path worked, but the selected canonical dataset contained no matching rows. |
| Explicit `not_established` | 30 | The compatibility shell refused to invent unrecovered semantics. |
| Capability unavailable | 1 | The declared live-only capability was disabled in the offline interface. |
| Canonical v1 source unavailable | 1 | The only identifier allowed by the frozen v1 contract was not present. |
| Resource-limit dead end | 1 | The maximum contract-valid request was still rejected because more rows existed and no paging path was available. |
| Frozen-schema incompatibility | 15 | The tool was called with an actual Stage 10 series, but its v1 contract accepted only the obsolete `quant_data.timeseries@1.0.0` schema. |

### Non-empty useful results

- `market.get_available_ticker`: 621 current tickers; the response correctly
  warns that the universe is not point-in-time.
- `market.get_price_series`: four OHLC series; the example request was
  intentionally truncated and reports unresolved adjustment, session-calendar,
  and local-capture semantics.
- `market.get_volume_series`: one provider-native volume series; the example
  request was intentionally truncated and reports unresolved unit, adjustment,
  session-calendar, and local-capture semantics.
- `macro.get_release_calendar`: 1,000 of 43,742 retained events. The response
  was truncated and exposes no continuation cursor, although callers can reduce
  scope with supported filters.
- `macro.release_surprises`: 55 records and 110 lineage references, with
  explicit historical-consensus and GDP-source warnings.
- `company.search_issuers`: one result for CIK `0000320193`.
- `company.get_fundamentals`: 682 records for CIK `0000320193`.
- `stats.distribution_diagnostics`: eight records over actual AAPL returns.
- `stats.bootstrap_confidence_interval`: one deterministic, seeded interval
  over actual AAPL returns.
- `stats.covariance_matrix`: sample covariance and correlation matrices over
  actual AAPL/MSFT returns.
- `stats.principal_components`: covariance, correlation, eigenvector, loading,
  and score matrices over actual AAPL/MSFT returns.

### Successful but empty selections

- `macro.align_us_recessions`
- `company.get_corporate_actions`
- `company.get_earnings_calendar`
- `company.get_consensus_history`
- `company.get_guidance_history`
- `news.search` for `Apple`

These are data-coverage outcomes, not runtime failures.

### Explicitly not established

The following 29 recovered compatibility routes returned their intended
`not_established` result under registry `2.50.0`:

- Macro: `macro.search_series`, `macro.describe_series`,
  `macro.revision_analysis`, `macro.standardize_surprises`,
  `macro.get_liquidity_snapshot`, `macro.get_liquidity_impulse`,
  `macro.get_credit_conditions`, and `macro.regime_snapshot`.
- Econometrics: `econometrics.structural_breaks` and
  `econometrics.local_projection`.
- Company: `company.get_share_count_history`,
  `company.get_estimate_revisions`, and `company.get_earnings_setup`.
- Energy: `energy.get_electricity_retail_sales` and
  `energy.get_weekly_fundamentals`.
- Market: `market.search_instruments`, `market.get_returns`,
  `market.get_forward_returns`, `market.technical_indicators`, and
  `market.cross_sectional_performance`.
- Rates: `rates.get_funding_conditions`, `rates.get_repo_facility_usage`, and
  `rates.curve_analytics`.
- Options: `options.search_captures`, `options.search_contracts`,
  `options.surface_diagnostics`, `options.screen_contracts`, and
  `options.strategy_scenario`.
- Research: `research.liquidity_credit_state`.

`options.get_surface_snapshot` was the thirtieth explicit
`not_established` result because the frozen fixture instrument identity did not
resolve to a usable selected surface.

### Other negative outcomes

- `macro.get_intraday_releases` returned `capability_unavailable`, as declared
  for the offline interface.
- `macro.get_series` accepts only
  `fixture:philadelphia_fed_rtdsm:EMPLOY`; the populated canonical store did not
  contain that frozen fixture identity, so the call returned
  `invalid_request: Requested macro series is unavailable`.
- `company.search_filings` rejected the maximum schema-valid `limit=500` with
  `resource_limit: Company filings exceed the query limit`. The v1 search
  contract has no date, form-type, or cursor pagination that can retrieve this
  issuer's retained filing set.

### Frozen analytics are not composable with current data

Each of these 15 v1 tools was called separately with the actual, byte-preserved
Stage 10 return-series object:

- `timeseries.transform`, `timeseries.describe`, `timeseries.align`, and
  `timeseries.correlation`;
- `econometrics.regression`, `econometrics.stationarity`, and
  `econometrics.rolling_regression`;
- `research.point_in_time_panel`, `data.quality_audit`,
  `research.event_study`, `alpha.signal_diagnostics`,
  `research.walk_forward_backtest`, `research.robustness_suite`,
  `stats.multiple_testing`, and `forecast.evaluate`.

All 15 returned structured `invalid_request` results because their frozen v1
schemas accept only `quant_data.timeseries@1.0.0`. The only public producer of
that schema is the unavailable hard-coded `macro.get_series@1` fixture route.
Consequently, these v1 kernels have no honest populated-database composition
path. Removing fields from a current series would discard provenance and is not
an acceptable workaround.

## Interoperability finding

The four native statistics tools do accept the complete Stage 10 return-series
contract, but one ordinary Python JSON parse-and-reserialize round trip caused
`Lineage digest mismatch`. Passing the exact serialized series object without
numeric reserialization succeeded. This means the current digest boundary is
too fragile for ordinary external-agent composition even when the semantic
payload is unchanged.

## V2 priorities derived from the audit

1. **Repair composition before adding algorithms.** Define one canonical
   analysis-series boundary for current market and macro series, make lineage
   digest verification invariant to standards-compliant JSON number
   round-tripping, and add a server-side source-reference or lossless adapter
   path. Add executable round-trip tests using common JSON clients.
2. **Validate the already-advertised successors on canonical data.** Audit the
   existing v2 macro discovery/series, time-series, econometrics, market-return,
   technical-indicator, structural-break, and data-quality variants before
   declaring additional v2 coverage.
3. **Fix canonical discovery and pagination gaps.** Prioritize
   `market.search_instruments@2`, `company.search_filings@2`, and
   `macro.get_release_calendar@2`, with stable cursors and bounded date/type
   filters. These unlock data that already exists.
4. **Build data-backed analytical successors next.** Implement
   `market.cross_sectional_performance@2`, then macro revision/surprise,
   liquidity/credit/regime, rates-curve, and company-fundamental analytics over
   retained canonical datasets.
5. **Defer tools without populated inputs.** Options-surface analytics,
   corporate actions, earnings/consensus/guidance analytics, energy tools,
   event studies, signal diagnostics, walk-forward tests, multiple testing, and
   forecast evaluation should follow verified data coverage and typed input
   availability rather than returning richer placeholders.

## Validation and limitations

- The focused immutable-reader regression suite passed 12 of 12 tests.
- Affected canonical-reader suites passed 84 of 84 tests before the final four
  adversarial cases were added; an independent verifier also passed 86 adjacent
  tests, 32 Stage 10/CLI tests, and direct fault injection of cleanup, sidecar
  drift, SQLite-open error mapping, and active-WAL separation.
- A broad offline suite ran 1,179 tests but was not a clean exit: seven failures
  arose in concurrently changing IWM/registry work. The independently rerun
  affected Stage 10 module passed 5 of 5, and none of those failures traversed
  the immutable reader.
- This checkpoint proves observed behavior for the stated inputs and retained
  data. It does not claim that empty datasets are complete or that declared
  warnings have been resolved.
