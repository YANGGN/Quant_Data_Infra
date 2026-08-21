# Quant Data Infrastructure — Recovery and Rebuild Plan

Last reconstructed: 2026-08-09  
Workspace: /home/volatility/Python_Projects/Quant_Data_Infra  
Document purpose: functional specification and rebuild plan after loss of the project files and databases

## 1. Recovery status and evidence

The workspace was inspected before this document was created. The original Git repository, Python source, migrations, databases, CSV inputs, tests, README, environment file, and prior plan.md were absent. The only files left on disk were two Vite optimizer-cache metadata files under sites/quant-data-atlas/.vite/deps. Those files do not identify the original frontend framework or dependencies.

A still-running, read-only dashboard process provided a second recovery source:

- Command: python3 -m quant_data dashboard --host 127.0.0.1 --port 8765
- Runtime: Python 3.11.11
- Server header: QuantDataDashboard/0.1 Python/3.11.11
- Persistence technology visible in the process: SQLite
- Public registry: API version 1.0, read-only execution, 57 agent tools
- Logical database health domains: macro, market, company, and news
- The live process has no open database or source-file descriptor from which deleted data or DDL can be recovered.

This plan uses four evidence labels:

- Recovered: established by the live process or the surviving filesystem.
- Remembered: established by the shared project conversation and prior implementation work, but no longer independently verifiable from source files.
- Target: the intended parity behavior for the rebuild.
- Proposed: a sensible reconstruction choice where the exact historical implementation is lost.

The exact old DDL, every physical table name, the remaining six members of the nine-index manifest, and the latest frontend source layout are not recoverable. This document does not invent them as historical facts. Logical table names below are rebuild targets unless explicitly described as remembered names.

Coordination note: plan.md did not exist when work began. The primary recovery task reserved this file as the sole writer, and all delegated agents were instructed to inspect read-only and not touch it.

## 2. Product mission

Quant Data Infrastructure is a local, provider-neutral data platform for all other quantitative strategies and analytical agents. It should:

1. Centralize repeatable market, macroeconomic, company, options, energy, rates, and news data.
2. Preserve enough provenance and availability metadata to support point-in-time research.
3. Give agents a fixed, safe, read-only tool surface instead of raw SQL access.
4. Provide a local web portal for inspecting stored data, ingestion status, and tool outputs.
5. Keep operation simple: Python plus SQLite, local files, explicit migrations, and manual ingestion commands before any scheduler is introduced.

This is research infrastructure, not an execution engine, order-management system, real-time market-data plant, or multi-tenant production warehouse.

## 3. Decisions already made

| Decision | Recovered intent |
| --- | --- |
| Database engine | SQLite |
| Database topology | Separate domains rather than one ever-growing file |
| Daily prices | One shared daily-price fact table for ETFs, single-name equities, and directly supported indices |
| Instrument type | Store ETF/equity/index in instrument metadata; do not create a table per ticker or a separate fact table per asset type |
| Company classification | Store sector and industry in a dedicated security/classification mapping |
| Macro versus market | Keep macro data separate from the large daily-price database |
| Date policy | Preserve source date values as ISO strings at their stated precision; do not perform timezone conversion |
| Macro revisions | Support latest, as-of, and defensible first-release policies where the source permits them |
| SOMA scope | Summary history only; CUSIP-level holdings are excluded from both storage and sourcing |
| Web portal | Read-only, local, fixed endpoints, persistent top navigation |
| Agent access | Fixed allowlisted JSON tools; no raw SQL and no caller-selected database path |
| Scheduling | Deliberately deferred; rebuild manual, idempotent jobs first |

## 4. System architecture

### 4.1 Logical database domains

The live health endpoint confirms four domains. The macro and market filenames are remembered; the company and news filenames are proposed for consistency because their exact paths are lost.

| Domain | Target file | Main contents |
| --- | --- | --- |
| market | data/market_data.sqlite | Instruments, identifiers, universes, daily OHLCV, classifications, returns inputs; proposed home for option captures/contracts/quotes |
| macro | data/macro_data.sqlite | Canonical macro series, versioned observations, releases, rates, curves, SOMA summaries, EIA, recession chronology, source snapshots |
| company | data/company_data.sqlite | SEC issuers/filings/facts, dated identifiers, corporate actions, shares, earnings events, estimates, guidance |
| news | data/news_data.sqlite | Immutable news and regulatory events, tags, symbols, topics, retraction state, point-in-time availability |

A legacy data/quant_data.sqlite may be recognized only by a future import/migration command if an old backup appears. New writes should use the split databases.

Cross-database relationships use stable application identifiers:

- CIK is the permanent SEC issuer identity.
- instrument_id is the canonical market identity.
- Provider ticker/symbol is a dated identifier, not a permanent company identity.
- series_id is the canonical macro identity.
- capture_id identifies one immutable option-surface acquisition.
- news_item_id identifies one locally captured immutable item.

SQLite cannot enforce foreign keys across files. Composite tools should join bounded result sets in application code under one explicit availability cutoff rather than silently creating weak cross-file relationships.

### 4.2 Component flow

    Provider APIs and controlled CSV inputs
                    |
            source-specific adapters
                    |
      raw response metadata + normalization
                    |
          transactional SQLite writers
                    |
       four domain databases and catalogs
                    |
       provider-neutral read/query services
                    |
      fixed 57-tool JSON registry and APIs
                    |
       local dashboard and quant agents

The ingestion path is the only normal writer. The dashboard and analytical tools are readers. Statistical functions should operate on typed in-memory series and should not mutate storage.

### 4.3 SQLite operating policy

Every writer should:

- Enable PRAGMA foreign_keys = ON.
- Use WAL mode where the filesystem supports it.
- Configure a bounded busy timeout.
- Stage and validate before opening a short write transaction.
- Use bound parameters exclusively.
- Commit canonical rows and the run/checkpoint record atomically.
- Roll back the whole logical batch on validation or write failure.

Every dashboard connection should:

- Open with SQLite URI mode=ro.
- Set PRAGMA query_only = ON.
- Use fixed queries, allowlisted sort fields, pagination, and bound filters.

## 5. Database design

The names in this section are the target logical contract. Remembered canonical names include macro_series, macro_observation_versions, macro_source_snapshots, and macro_source_artifacts. Exact old migrations are gone.

### 5.1 Shared control and provenance pattern

Each database should carry a small local control plane:

#### datasets

- dataset_id
- provider
- dataset_name
- description
- expected_frequency
- revision_policy
- active
- created_at
- updated_at

#### ingestion_runs

- run_id
- dataset_id
- command
- requested_start
- requested_end
- started_at
- completed_at
- status: running, succeeded, partial, or failed
- rows_fetched
- rows_inserted
- rows_updated
- rows_unchanged
- rows_rejected
- error_summary
- code/schema version

#### source_snapshots or source_artifacts

- snapshot_id
- dataset_id
- source locator or endpoint
- requested parameters with secrets removed
- fetched_at
- source-published date when known
- HTTP status and selected response metadata
- content hash
- raw artifact path or compressed payload, if retention is allowed
- run_id

An exact replay with the same natural key and content should be a no-op. Secrets must never be persisted in request metadata, logs, errors, or the portal.

### 5.2 Market database

#### instruments

One row per canonical tradable or index instrument:

- instrument_id
- canonical_symbol
- display_name
- asset_type: equity, etf, index, or another explicit type
- exchange
- currency
- country
- active
- first_seen_at
- last_seen_at

#### instrument_identifiers

Provider identifiers are versioned because tickers can change:

- instrument_id
- provider
- provider_symbol
- valid_from
- valid_to
- confidence or confirmation state
- source_snapshot_id

The natural key is provider plus provider_symbol plus validity range. A ticker must never be treated as a permanent issuer identity.

#### universes and universe_memberships

Track where a requested population came from:

- universe_id, name, source_file, source_hash, imported_at
- instrument_id, universe_id, valid_from, valid_to, source_row

Remembered universe inputs:

- ETF deep diversified liquid_2026-07-20.csv
- Major Index Liquid_2026-07-20.csv

#### daily_prices

All ETFs, single-name equities, and indices belong in this same fact table:

- instrument_id
- trade_date as ISO YYYY-MM-DD text
- provider
- price_variant, such as raw or adjusted
- open
- high
- low
- close
- adjusted_close when supplied
- volume
- provider_vwap when supplied
- source_snapshot_id
- run_id
- fetched_at

Target natural key: instrument_id, trade_date, provider, and price_variant.

Required indexes:

- instrument_id plus trade_date descending
- trade_date plus instrument_id
- provider plus provider symbol through the identifier mapping

No per-ticker tables. No separate ETF and single-name price fact tables. Asset type filtering comes from instruments.

#### security_classifications

The remembered requirement was a mapping table for the sector and industry of single names:

- instrument_id
- sector
- industry
- classification_provider
- observed_at or effective_from/effective_to
- source_snapshot_id

The initial implementation may retain only current classification; the schema should permit effective dating so future backtests do not assume today's classification existed historically.

#### Option data

The later live registry proves that immutable option-surface workflows existed, but it does not expose their old physical database. Placing them in the market database is a rebuild proposal, not a recovered fact. Rebuild these logical entities:

- option_captures: capture_id, underlying instrument, requested feed, resolved feed, request/completion times, availability boundary, completeness, source snapshot
- option_contracts: canonical contract identity, underlying, expiration, strike, call/put, provider identifier, status
- option_capture_membership: contracts included in a capture and explicit missing/exclusion state
- option_quotes: bid/ask, sizes, last, volume and timeframe, open interest, implied volatility, Greeks, quote timestamp/age, underlying spot

An option surface must contain one immutable capture and one resolved feed. Never mix quotes from separate capture times or silently fit missing values.

### 5.3 Macro database

#### macro_series

Canonical catalog fields:

- series_id
- provider and provider_series_id
- display_name and description
- category/domain
- frequency
- unit
- value_representation
- provider_unit
- scale_factor_to_base_unit
- base_unit
- seasonal adjustment
- dimensions
- supports_vintages
- supported_vintage_modes
- source notes and quality warnings
- active and coverage metadata

Remembered series identifiers include:

- philadelphia_fed_rtdsm:EMPLOY
- gdp:us:real_qoq_saar_pct
- gdp:us:nominal_billions
- treasury:fmp:par_yield:<tenor> for twelve tenors

#### macro_observation_versions

- series_id
- period_start
- period_end
- value, nullable for explicit missing observations
- value_status or missing_reason
- unit and base-unit conversion metadata
- vintage_at
- available_at
- availability_precision
- revision_sequence
- is_preliminary
- quality_flags
- provenance/source_snapshot_id
- ingested_at

Target uniqueness is series, reference period, and source vintage/revision identity. A changed source value becomes a new version when true vintage history is supported; an identical replay is a no-op.

#### macro_source_snapshots and macro_source_artifacts

These remembered tables record the request, capture time, source-published metadata, content hash, artifact lineage, and ingestion run. They are essential for explaining why a value was considered available.

#### Source-specific canonical or staging families

The canonical tools should read macro_series plus macro_observation_versions. Adapters may additionally use narrowly scoped staging tables for:

- GDP vintages and release stages
- Philadelphia Fed RTDSM vintages
- NY Fed SOMA weekly summary categories
- FMP Treasury par-yield tenors
- official overnight funding rates and distributions
- repo-facility usage
- Federal Reserve balance-sheet and Treasury General Account components
- NBER monthly recession chronology
- EIA monthly/annual electricity retail observations
- EIA weekly petroleum and natural-gas fundamentals
- stored economic-calendar consensus and actual releases

CUSIP-level SOMA holdings must not be recreated. Only summary-level date/category observations are in scope.

### 5.4 Company database

The later tool registry establishes these logical entities even though exact DDL is lost:

#### issuers and dated identifiers

- SEC CIK as permanent filer identity
- legal name and issuer metadata
- dated ticker/provider-symbol links
- link source, confidence, and validity

#### sec_filings

- immutable accession number
- CIK
- form
- filing date
- acceptance timestamp or explicit date-only availability
- report period
- primary document and source URL
- local capture metadata

#### sec_companyfacts and concept mappings

- source concept, taxonomy, unit, fiscal period, filed/accepted availability, accession
- internal metric mapping and mapping version
- remembered mapping version: sec-core-v2

Instant shares and fiscal-period weighted-average shares must stay separate.

#### corporate_action_versions

FMP cash dividends and splits were the implemented scope. Store provider-symbol identity, event terms, event date, local capture availability, source snapshot, and version. Never claim historical availability from the event date when the row was only captured later.

#### Earnings research families

- earnings_event_snapshots
- consensus_snapshots and consensus_values
- management_guidance_versions
- explicit event-to-fiscal-period links

Consensus from a current provider feed is available from local capture unless independent publication evidence exists. Same-date candidate event collisions must fail closed unless an explicit event key selects one. Guidance needs a source link, target period, value shape, review state, and availability.

### 5.5 News database

The target model is immutable local capture with explicit retraction state:

- news_items: provider/source item ID, source kind, headline, body/summary, source URL, published_at, available_at, captured_at, event label, retracted/superseded state
- news_item_symbols: dated instrument/provider-symbol associations
- news_item_topics
- news_item_geographies
- news_item_coverage_lanes
- news_snapshots and ingestion runs

Use SQLite FTS5 for full-text search if available, backed by normal relational filters. Point-in-time search must filter on local availability, not only the article's claimed publication time.

## 6. Time, date, availability, and vintage contract

This contract is central to the project and must be implemented once and reused everywhere.

### 6.1 Date handling

- Store calendar dates as ISO YYYY-MM-DD text.
- Preserve source offset-aware timestamps as received when timestamp precision is real.
- Treat values as already expressed in the source's stated Eastern/local convention.
- Do not convert stored dates or timestamps to another timezone.
- Do not invent midnight timestamps for date-only values.
- Record availability_precision as date, datetime, inferred, or unknown where needed.
- Parse only enough to validate and compare the declared ISO representation; serialize the original canonical value.

Start and end filters are inclusive calendar-date filters. A query for observations in 2026 must compare normalized ISO date fields as dates, not mixed Python datetime objects. The regression test must specifically cover monthly observations in a 2026 start/end range.

### 6.2 Vintage modes

- latest: newest currently stored version for each reference period.
- as_of: newest version actually available no later than the caller's explicit cutoff.
- first_release: earliest defensible source release, not merely the first row captured locally.

The public as_of boundary accepts an ISO calendar date or an offset-aware timestamp. Its stored and query timezone representations are not converted.

If a source has only current-state captures, the tool must either support local-capture as-of semantics explicitly or reject unsupported historical vintage modes. It must not present a current value as historical truth.

Known caveats:

- Treasury par yields were current/latest-state data; corrections could overwrite the same date and true historical vintages were unavailable.
- First stored SOMA capture is not automatically the original release.
- Philadelphia Fed and GDP preliminary flags must be based on defensible release stages, not every historical row.
- FMP corporate actions, consensus, and earnings-calendar history are available from local capture unless independent evidence proves earlier availability.
- Revision analysis is ex-post and is not a point-in-time-safe trading feature.

### 6.3 Missingness and quality

Missing periods remain explicit with value = null and a reason/quality flag. Analytical code must not silently:

- forward-fill,
- resample,
- substitute adjusted for unadjusted prices,
- interpolate a yield curve,
- convert currency,
- combine instant and average share counts,
- mix option captures,
- or treat missing evidence as neutral.

Any fill, aggregation, classification, or exclusion policy must be a caller-visible parameter and appear in the output audit.

## 7. Data sources

### 7.1 Remembered and recovered providers

| Provider/input | Data | Typical cadence | Storage policy |
| --- | --- | --- | --- |
| Financial Modeling Prep (FMP) | Daily OHLCV, index history, company profiles/classifications, cash dividends, splits, earnings calendar, consensus, current macro calendar/releases, Treasury par yields | Daily or capture-based | Prices/current state upsert; locally captured histories versioned by capture; one intraday-release tool is deliberately non-persistent |
| ETF deep diversified liquid_2026-07-20.csv | ETF universe | Controlled import | Hash input and version universe membership |
| Major Index Liquid_2026-07-20.csv | Liquid major-index/single-name universe | Controlled import | Hash input and version universe membership |
| U.S. Bureau of Labor Statistics | Labor and inflation series | Monthly/periodic release | Canonical macro observations with release availability |
| Bank for International Settlements | Leverage, credit, and debt-service series | Quarterly or provider cadence | Version-aware macro observations |
| Chicago Fed | Financial conditions and related macro series | Weekly/monthly by series | Canonical macro observations |
| Philadelphia Fed RTDSM | Real-time macro vintages, including EMPLOY | Vintage files by release | Preserve vintage and first-release semantics |
| GDP vintage source | Real and nominal GDP vintages/release stages | Quarterly | Append defensible release versions |
| New York Fed SOMA | Weekly summary history | Weekly | Summary-only; no CUSIP detail |
| New York Fed official markets data | EFFR, OBFR, TGCR, BGCR, SOFR distributions/index and ON RRP/SRP facilities | Business day | Exact-date observations; no fill |
| Federal Reserve H.4.1 | Balance-sheet liquidity components | Weekly | Version-aware/current source policy documented per series |
| U.S. Treasury Fiscal Data | Treasury General Account alternative | Business day/provider cadence | Preserve source variant; never blend silently with H.4.1 |
| NBER | Official monthly U.S. recession chronology | Irregular determinations | Monthly classification chronology |
| U.S. EIA | Electricity retail sales/price/revenue/customers and weekly petroleum/gas fundamentals | Monthly, annual, or weekly | Snapshot membership and revision-aware series |
| SEC EDGAR | Issuers, submissions, immutable filings, CompanyFacts | Daily/continuous | CIK identity; acceptance-time availability |
| News/regulatory feeds | Searchable immutable news and regulatory events | Capture-based | Local availability and retraction state |
| Option-chain provider feeds | Immutable option-surface captures | On demand/capture-based | One resolved feed per capture; no mixed snapshots |

The exact endpoint paths, licenses, request limits, option/news provider manifests, and GDP provider must be revalidated before rebuilding adapters.

### 7.2 Nine directly available index instruments

The prior decision was to implement nine index instruments directly available from FMP and skip indirect/proxy series. The exact manifest was lost.

Known from the shared conversation:

1. S&P 500, explicitly referenced as FMP symbol ^GSPC
2. VIX concept; exact provider symbol must be revalidated
3. MOVE concept; exact provider symbol must be revalidated

Do not guess the remaining six or silently substitute ETFs. Rebuild a reviewed config/index_instruments file with exactly nine rows before the index backfill is rerun. Store canonical name, FMP symbol, asset type, currency, source, active flag, and validation date.

### 7.3 Refresh and write policy

Automation remains out of scope until manual commands are reliable. The intended future cadence is:

| Dataset | Suggested trigger | Incremental window | Write behavior |
| --- | --- | --- | --- |
| Daily prices and index bars | After provider's end-of-day data settles | Last 5–10 trading days plus missing history | Upsert natural key; update only changed fields |
| FMP profiles/classifications | Universe import and weekly/monthly refresh | Current universe | Upsert current mapping; retain observed/effective history when possible |
| Treasury yields/funding rates | Business day after publication | Recent business days | Upsert exact dates; do not claim true vintages |
| SOMA summary | Weekly after publication | Several recent weeks | Upsert corrected summaries; no CUSIP rows |
| BLS/Chicago Fed/BIS | Source release schedule | Revision window appropriate to series | Append version when source supports revisions |
| GDP/RTDSM | Each new vintage file | Full affected vintage | Append vintage; exact duplicate no-op |
| SEC filings/facts | Daily or more often | Since last successful accession/capture | Immutable accessions; append fact versions |
| Earnings calendar/consensus | Daily and more often near events | Current/recent event horizon | Append capture state; never backdate availability |
| EIA | After weekly/monthly release | Current and recently revised periods | Snapshot-aware upsert/version |
| Options | Explicit on-demand capture | One acquisition | Immutable capture, contracts, quotes, completeness ledger |
| News | Future polling job | Since checkpoint with overlap | Immutable item capture; version retractions |

## 8. Ingestion and mutation semantics

Each adapter should follow the same stages:

1. Validate configuration, universe, date range, and provider limits.
2. Start an ingestion run.
3. Fetch with bounded retries, backoff, timeout, and rate-limit handling.
4. Record redacted request metadata and content hash.
5. Normalize provider fields into canonical types while preserving source values needed for audit.
6. Validate keys, dates, OHLC invariants, units, duplicates, and completeness.
7. Write in one short transaction using the dataset's declared policy.
8. Record counts, warnings, rejected rows, and checkpoint only after commit.

Mutation policies:

- Current keyed facts, such as daily bars: insert new keys; update an existing key when the provider corrects it; no table-wide replacement.
- Versioned macro/company facts: append a new source vintage/capture when content changes; exact duplicate is a no-op.
- Immutable events, filings, option captures, and source artifacts: insert once under a stable provider or content identity.
- Retractions/deletions: preserve the old item and append explicit inactive/retracted/tombstone state.
- Failed/partial run: never advance the successful checkpoint.
- Backfill: use the same code path and constraints as incremental ingestion.

All commands need dry-run/validation output where practical. Never log FMP_API_KEY or another credential. Recreate only .env.example in source control; the real .env remains local.

## 9. Agent tool platform

### 9.1 Registry contract

Recovered live contract:

- API version: 1.0
- Tool count: 57
- Execution: read_only
- Every tool has a fixed JSON input schema, example arguments, and output schema.
- All 57 recovered top-level input schemas set additionalProperties = false.
- No arbitrary SQL or caller-selected file path is accepted.
- The only live-provider, non-persistent tool is macro.get_intraday_releases; it requires FMP_API_KEY and fetches one Eastern calendar day into memory.

Remembered API routes:

- GET /api/agent-tools
- POST /api/agent-tools/call

The call endpoint should retain an 8 MiB request cap, strict UTF-8 JSON, structured validation errors, and valid RFC JSON. NaN and Infinity must be rejected or converted to null with warnings before serialization.

### 9.2 All 57 recovered tools

#### Macro — 12

- macro.search_series — discover canonical series by text and metadata
- macro.describe_series — metadata, coverage, dimensions, vintage capabilities, and warnings
- macro.get_series — native observations under latest, as_of, or first_release
- macro.get_intraday_releases — current one-day FMP release fetch, memory only
- macro.release_surprises — CPI, payroll, or GDP actual minus stored consensus
- macro.revision_analysis — first-release versus newest-value revision statistics
- macro.align_us_recessions — align official NBER monthly chronology to a TimeSeries
- macro.standardize_surprises — standardize surprises using strictly prior errors
- macro.get_liquidity_snapshot — point-in-time Fed/Treasury liquidity components and transparent proxies
- macro.get_liquidity_impulse — component-decomposed exact-date liquidity changes
- macro.get_credit_conditions — Chicago Fed, NY Fed CMDI, BIS leverage/debt-service evidence
- macro.regime_snapshot — deterministic growth/inflation regime with optional conditions and curve overlays

#### Time series — 4

- timeseries.transform
- timeseries.describe
- timeseries.align
- timeseries.correlation

#### Econometrics — 5

- econometrics.regression
- econometrics.stationarity
- econometrics.rolling_regression
- econometrics.structural_breaks
- econometrics.local_projection

#### Company — 10

- company.search_issuers
- company.search_filings
- company.get_fundamentals
- company.get_corporate_actions
- company.get_share_count_history
- company.get_earnings_calendar
- company.get_consensus_history
- company.get_guidance_history
- company.get_estimate_revisions
- company.get_earnings_setup

#### Energy — 2

- energy.get_electricity_retail_sales
- energy.get_weekly_fundamentals

#### Market — 5

- market.search_instruments
- market.get_returns
- market.get_forward_returns
- market.technical_indicators
- market.cross_sectional_performance

#### Rates — 3

- rates.get_funding_conditions
- rates.get_repo_facility_usage
- rates.curve_analytics

#### Options — 6

- options.search_captures
- options.search_contracts
- options.get_surface_snapshot
- options.surface_diagnostics
- options.screen_contracts
- options.strategy_scenario

#### Research, diagnostics, forecast, news — 10

- research.point_in_time_panel
- data.quality_audit
- research.event_study
- alpha.signal_diagnostics
- research.walk_forward_backtest
- research.robustness_suite
- stats.multiple_testing
- forecast.evaluate
- news.search
- research.liquidity_credit_state

### 9.3 Time-series core

Use typed immutable TimeSeries and Observation objects with:

- series metadata,
- period_start and period_end,
- explicit nullable value/missing reason,
- available_at,
- quality flags,
- source provenance,
- transformation lineage.

Recovered transform methods:

- difference
- drawdown
- expanding_zscore
- index_to_100
- log_change
- percent_change
- qoq_percent
- qoq_saar
- rolling_mean
- rolling_std
- rolling_zscore
- yoy_percent

Percent values are percentage points, while log change and drawdown are decimals. Calendar transforms require the exact prior calendar-period key; they do not treat an arbitrary preceding row as the prior month/quarter/year.

### 9.4 Statistical and econometric behavior

timeseries.describe:

- coverage and inferred/explicit missingness
- count, mean, variance/std, robust quantiles
- lag-one autocorrelation
- median/MAD-based outliers
- finite-number validation

timeseries.align:

- inner or outer join
- explicit target frequency: daily, weekly, monthly, quarterly, semiannual, or annual
- explicit downsample aggregation: first, last, mean, sum, min, or max
- missing policy: none, drop, or bounded forward_fill
- forward-fill age counts true calendar-frequency buckets, not sparse joined rows
- upsampling is rejected

timeseries.correlation:

- Pearson or Spearman
- pairwise sample counts
- optional rolling estimates
- lead/lag convention: a positive lag means x leads y and pairs x[t] with y[t+lag]
- recovered limits: at most 101 unique lags, absolute lag 252, 25,000 rolling estimates, and 5,000,000 lead/lag row evaluations
- mixed source frequencies require explicit alignment/aggregation

econometrics.regression:

- inner-aligned OLS
- intercept optional
- classical or Bartlett Newey–West HAC covariance
- robust centered/scaled computation mapped back to native coefficients
- uncentered R-squared when no intercept is included
- fitted values optional
- listwise deletion, no implicit fill
- warnings for calendar gaps and release-time/look-ahead risk

econometrics.stationarity:

- ADF and KPSS with constant or constant-plus-trend deterministic terms
- Phillips–Perron remains explicitly unavailable until a validated dependency is adopted
- joint interpretation rather than a single overconfident label

Revision analysis compares first release with the latest currently stored observation and reports bias, MAE, RMSE, positive/negative/unchanged revisions, and sign changes with paired provenance. It is explicitly ex-post.

### 9.5 Market and research analytics

Recovered technical indicators:

- autocorrelation oscillator
- CM MACD Ultimate multi-timeframe adaptation
- EMA, SMA, and WMA
- KDJ
- MACD
- provider VWAP and rolling daily-bar VWAP
- PSAR
- RSI
- smart money concepts
- squeeze momentum
- supertrend
- supertrend AI clustering
- swing structure forecast
- WaveTrend LazyBear
- Williams VIX Fix

Daily bars cannot reconstruct true intraday session VWAP or Pine intraday security calls. Outputs must state this limitation.

Cross-sectional performance:

- caller-supplied universe versus one benchmark
- exact benchmark-calendar anchors
- simple price returns, no fill and no FX conversion
- ranks, breadth, dispersion, leadership, and relative-return series
- recovered caps: 50 members, 10 horizons, horizon 252, 500 evaluation dates, and 100,000 output cells

Point-in-time and research rules:

- point-in-time panels use only values available at each decision cutoff
- event studies use explicit events, return series, windows, and session convention
- signal diagnostics report IC, quantile returns, monotonicity, turnover, and cost-aware proxies
- walk-forward backtests use chronological folds with purge, embargo, execution lag, availability policy, turnover, and costs
- robustness uses deterministic circular-block bootstrap and era checks
- multiple-testing tools expose Bonferroni, Holm, BH, and BY
- forecast evaluation supports exact-period losses and optional HAC Diebold–Mariano comparison

### 9.6 Rates, liquidity, company, and options semantics

Funding metrics recovered from the registry include EFFR, OBFR, TGCR, BGCR, SOFR, IORB, target bounds, SOFR volume/percentiles/averages/index, and transparent spreads/widths. Facility scope is ON_RRP and SRP. Curve metrics include 10y–2y, 10y–3m, 30y–5y, the 2s5s10s butterfly, and 2s10s30s curvature. Missing dates are never filled or interpolated.

Company identity and availability rules:

- CIK identifies the filer.
- Ticker is a dated discovery/provider key.
- SEC filing availability uses exact acceptance time or an explicit conservative date-only rule.
- FMP corporate actions implement cash dividends and splits.
- Instant and weighted-average shares remain separate.
- Earnings setup uses one connection and one cutoff for every component.
- Fiscal links are stored or caller-supplied, never guessed.

Options rules and recovered caps:

- capture discovery: 500 captures
- contract discovery: 2,000 contracts
- one surface: 5,000 contracts and a single capture
- moneyness/delta filtering requires a capture
- screening produces a complete exclusion ledger
- strategy analysis uses same-capture legs, explicit marks, exact expiry payoff, aggregate provider Greeks, and optional local Greek shocks

## 10. Web portal and API

### 10.1 Pages

Remembered routes:

- / — overview and database health
- /gdp-vintages — inspect GDP releases/vintages
- /table-inspector — allowlisted database/table browser
- /agent-tools — run tools and inspect structured output

The top navigation must be one shared, persistent component on every page, including Overview. Clicking a route must never make the navigation disappear.

The Agent Tools page should:

- discover forms from the fixed manifest,
- show tool description, assumptions, inputs, and limits,
- render summary records, matrices, coefficient tables, series, audits, warnings, and nested outputs structurally,
- provide raw valid JSON and copy,
- allow a fetched series to be reused by transform, describe, stationarity, regression, align, and correlation forms,
- show clear validation/data-unavailable errors without a stack trace.

### 10.2 Remembered read APIs

- /api/health
- /api/summary
- /api/filters
- /api/events
- /api/runs
- /api/datasets
- /api/gdp-vintages/summary
- /api/gdp-vintages/filters
- /api/gdp-vintages
- /api/macro/filters
- /api/macro-v2/filters
- /api/soma/filters
- /api/eia/filters
- /api/table-selector
- /api/table-inspector
- /api/market-summary
- /api/market-filters
- /api/prices
- /api/price-series
- /api/agent-tools
- /api/agent-tools/call

Later company/news/options behavior may have been tool-only; exact additional HTTP routes are not recoverable and should be generated from the new registry rather than guessed.

### 10.3 Recovered security headers

The dashboard returned:

- X-Content-Type-Options: nosniff
- X-Frame-Options: DENY
- Referrer-Policy: no-referrer
- Cache-Control: no-store
- Content-Security-Policy: default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'

Keep the portal bound to 127.0.0.1 by default. There is no browser SQL console and no browser ingestion endpoint.

## 11. Technology stack

| Layer | Recovered/target technology |
| --- | --- |
| Runtime | Python 3.11+; live process was 3.11.11 |
| Packaging | pyproject.toml and importable quant_data package; remembered console entry point quant-data = quant_data.cli:main |
| Persistence | SQLite through Python sqlite3 |
| Core analytics | Deliberately standard-library-heavy Python; no NumPy, pandas, SciPy, or statsmodels dependency was required by the earlier core |
| HTTP service | Python module command, local host; remembered lightweight standard-library server |
| UI | Remembered HTML/CSS/JavaScript dashboard; surviving Vite cache indicates a later or parallel site called quant-data-atlas but proves no framework/dependency |
| Configuration | .env for local secrets, especially FMP_API_KEY; .env.example in source control |
| Input formats | Provider JSON/CSV plus controlled universe CSV files |
| Tests | unittest-compatible suite with pytest execution where available |

Do not adopt a heavier framework merely because a Vite cache survived. First recover the dependency-light operational dashboard. If a separate Vite UI is intentionally rebuilt, choose and document its framework, lockfile, build, and hosting path explicitly.

### 11.1 Target package layout

    Quant_Data_Infra/
      plan.md
      README.md
      pyproject.toml
      .env.example
      config/
        index_instruments.*
        dataset_manifest.*
      inputs/
        universes/
      data/
        market_data.sqlite
        macro_data.sqlite
        company_data.sqlite
        news_data.sqlite
        artifacts/
      quant_data/
        __init__.py
        __main__.py
        cli.py
        database.py
        migrations/
        adapters/
        market/
        macro/
        company/
        news/
        options/
        timeseries.py
        timeseries_stats.py
        econometrics.py
        macro_analysis.py
        agent_tools.py
        dashboard.py
        static/
      tests/
        fixtures/
      scripts/

Remembered module names are timeseries.py, timeseries_stats.py, econometrics.py, macro_analysis.py, and agent_tools.py. Other layout names are proposed for clarity.

## 12. Quality, safety, and performance requirements

### 12.1 Data quality

- Validate OHLC relationships, nonnegative volume, duplicate keys, and sorted dates.
- Validate series unit/value representation and base-unit scale.
- Surface provider coverage rather than assuming a stale series is a date-parsing failure.
- For philadelphia_fed_rtdsm:EMPLOY, distinguish source coverage from local ingestion coverage; do not silently stop at 2024.
- Ensure SOMA summary history returns many weekly observations after backfill.
- Assert that no CUSIP-level SOMA tables, endpoints, or adapter branches exist.
- Audit gaps, staleness, outliers, vintages, last successful runs, and truncated results.

### 12.2 Numerical correctness

- Reject non-finite input and derived JSON values.
- Use numerically stable centered/scaled OLS or QR, not fragile raw normal equations.
- Use the correct centered/uncentered total sum of squares for intercept/no-intercept models.
- Keep robust moment/Jarque–Bera conventions internally consistent.
- Count real calendar periods for bounded forward fill.
- Warn when period-label alignment can be look-ahead because available_at is later.
- Require explicit handling for mixed-frequency correlation/regression.

### 12.3 Resource limits

Retain schema-visible limits so agents cannot accidentally freeze the server or UI:

- macro series observations: 10,000 per call
- multi-series inputs: 20 where applicable
- technical indicator specs: 20; returned bars: 10,000; output values: 200,000
- lead/lag and rolling correlation limits described above
- cross-sectional and options limits described above
- every paginated search has a bounded limit

The UI must render wide correlation matrices and nested rolling/lead-lag results intentionally rather than silently dropping columns or stringifying huge arrays into one cell.

## 13. Testing and acceptance

An earlier 10-tool milestone reportedly passed 219 tests. That is a historical baseline, not proof for the later 57-tool build. No tests survive, so the rebuild needs fresh contract, fixture, integration, and live smoke coverage.

### 13.1 Required test layers

1. Migration tests against a new temporary database and migration reruns.
2. Adapter fixtures with recorded/redacted provider responses.
3. Idempotency tests: insert, exact replay, corrected value, and failed transaction.
4. Point-in-time/vintage fixtures with known first/latest/as-of results.
5. Date tests for date-only and offset-aware values without timezone conversion.
6. Core numerical fixtures for describe, align, correlation, regression, stationarity, revisions, rolling/break/local-projection methods, event studies, and backtests.
7. Registry schema tests for all 57 names, examples, bounds, and output contracts.
8. Dashboard/API tests for JSON validity, limits, headers, persistent navigation, and read-only behavior.
9. Live-provider smoke tests that never expose secrets and are optional in offline CI.

### 13.2 Minimum parity acceptance checks

- The four databases initialize independently and health reports their actual state.
- The two remembered universe CSVs import with hashes and membership counts.
- All configured symbols backfill into one daily_prices table.
- ETFs, equities, and indices are distinguishable through instruments.
- Single names return sector and industry mappings.
- The reviewed nine-index manifest is complete; ^GSPC works; VIX/MOVE symbols are provider-validated.
- Macro get-series queries with start/end in 2026 return existing 2026 observations without timezone conversion.
- latest, as_of, and first_release fixtures return different expected vintages.
- SOMA has summary history and zero CUSIP-level schema/sourcing.
- Treasury yields expose all twelve configured tenors.
- SEC issuer/ticker identity tests survive ticker changes.
- Option surface tools never combine capture IDs.
- News as-of search excludes items not locally available by the cutoff.
- GET /api/agent-tools reports exactly the reviewed 57-tool manifest.
- All tool calls are read-only; only the explicit intraday release tool can access a live provider and it persists nothing.
- Navigation remains visible after clicking Overview and all remembered routes.
- Strict JSON serialization never emits NaN or Infinity.

## 14. Rebuild phases

### Phase 0 — Freeze the recovered contract

- Save the public 57-tool manifest from the live process as a redacted recovery artifact before the process exits.
- Record the exact security headers and server command.
- Initialize a new Git repository and branch protection/backup practice.
- Create README.md, pyproject.toml, .gitignore, and .env.example.
- Resolve the nine-index manifest and provider endpoint/licensing checklist.

Exit criterion: the recovery specification and public tool contract are version controlled.

### Phase 1 — Persistence foundation

- Create explicit ordered migrations for the four SQLite files.
- Implement connection factories, WAL/read-only policies, transactions, run records, snapshots, and content hashing.
- Add database initialization, migration status, backup, and integrity-check CLI commands.

Exit criterion: clean and repeated initialization passes migration/idempotency tests.

### Phase 2 — Restore market and macro sources

- Import both universe CSVs.
- Restore FMP instruments, profiles, daily prices, indices, and Treasury yields.
- Restore macro catalog/version model and BLS/BIS/Chicago Fed/RTDSM/GDP/SOMA/NBER/rates/EIA adapters.
- Re-run full backfills with source/run audit.

Exit criterion: the market and macro parity checks in Section 13 pass.

### Phase 3 — Restore company, options, and news

- Restore SEC issuer/filing/CompanyFacts ingestion and sec-core-v2 mappings.
- Restore corporate actions, shares, earnings events, consensus, guidance, and PIT rules.
- Restore immutable option capture and news/regulatory-event models after providers are revalidated.

Exit criterion: identity, local-availability, immutable-capture, and retraction tests pass.

### Phase 4 — Restore analytical core and all tools

- Rebuild typed TimeSeries contracts and deterministic transforms.
- Rebuild statistics/econometrics/research modules with numerical audit tests.
- Register all 57 tools under fixed schemas and resource bounds.
- Revalidate every example against fixture databases.

Exit criterion: registry, schema, numerical, and read-only tests pass for all tools.

### Phase 5 — Restore portal

- Recreate the local dashboard, persistent navigation, inspectors, run health, and tool output renderers.
- Restore the remembered API routes and the exact security/read-only posture.
- Decide separately whether quant-data-atlas should remain a dependency-light static UI or become a documented Vite application.

Exit criterion: browser tests pass and the portal cannot mutate a database.

### Phase 6 — Backfill verification and operations

- Compare row counts, date ranges, missingness, and sample values with providers.
- Run SQLite integrity checks and create recoverable backups.
- Document manual runbooks and incident recovery.
- Only then revisit scheduled automation, locks, retries, alerting, and release-calendar triggers.

Exit criterion: the system can be rebuilt from Git, configuration, migrations, and documented backfill commands on a clean machine.

## 15. Open recovery checkpoints

These require evidence or an explicit new decision before implementation:

1. Exact six missing FMP index instruments and the provider symbols for VIX and MOVE.
2. Exact GDP vintage provider/endpoint and historical release-stage mapping.
3. Exact physical schemas/migration numbering from the lost repository.
4. Exact option-chain and news/regulatory providers, licenses, and retention rights.
5. Whether option captures lived inside the market database or another physical SQLite file.
6. Whether EIA datasets had completed backfills; tools existed in the later registry, but population state is unknown.
7. Whether company_data.sqlite and news_data.sqlite were the old physical filenames; their logical domains are confirmed.
8. Whether the surviving quant-data-atlas Vite cache belonged to the main portal or a later/parallel UI experiment.
9. Exact later test count after expansion from 10 to 57 tools.
10. Any source credentials, account limits, and provider symbol mappings formerly held in the deleted .env.

## 16. Deferred work

- Scheduled jobs and external orchestration
- Intraday market bars and tick data
- Portfolio/order/execution management
- Automatic currency conversion
- Curve interpolation
- CUSIP-level SOMA holdings
- Silent imputation or hidden composite regime scores
- Phillips–Perron until a validated numerical dependency is deliberately accepted
- Multi-user authentication or internet exposure of the dashboard

## 17. Definition of restored

The project is restored when:

- Source, migrations, tests, and configuration templates are version controlled.
- Four independently recoverable SQLite databases implement the declared logical contracts.
- Manual ingestion is idempotent, auditable, and capable of complete backfill.
- Dates remain as-is without accidental timezone conversion.
- Point-in-time and local-capture limitations are explicit and tested.
- The reviewed 57-tool manifest is available, bounded, read-only, and produces valid JSON.
- The web portal exposes persistent navigation, data inspection, run health, and useful structured tool output.
- A clean environment can rebuild the databases from documented sources and can restore them from verified backups.

This plan preserves what can be recovered from project history and the live manifest while keeping every unresolved fact visible. It should be treated as the authoritative recovery specification until replaced by source-controlled migrations, schemas, and provider manifests.


## 18. Later-state integrated recovery addendum

This section records functionality implemented after the earlier design checkpoints
captured above. **It supersedes Sections 7.2, 7.3, 10, 13.2, 14 Phase 5-6,
15, 16, and 17 wherever those sections conflict with the facts below.** In
particular, the final project had known index/provider manifests, installed
scheduled jobs, confirmed split database filenames, an Algorithms page, a
Charts research UI, implemented Alpaca options and news ingestion, and a
separate private Quant Data Atlas.

The confidence terms in Section 1 still apply. Counts and performance figures
below are historical validation snapshots, not rebuild targets.

### 18.1 Final physical stores and environment names

The mature deployment used four operational SQLite databases:

| Domain | Exact remembered path | Override |
| --- | --- | --- |
| Market | data/market_data.sqlite | QUANT_MARKET_DB_PATH |
| Macro | data/macro_data.sqlite | QUANT_MACRO_DB_PATH |
| Company | data/company_data.sqlite | QUANT_COMPANY_DB_PATH |
| News | data/news_data.sqlite | QUANT_NEWS_DB_PATH |
| Legacy unified/rollback | data/quant_data.sqlite | QUANT_DB_PATH |

The split was by write/operational boundary. Equities, ETFs, and indexes shared
the market instrument master and prices_daily. Options were an implemented
market-database domain, not an unknown separate database. Company and news
database filenames are confirmed.

Each split store had its own migration ledger, dataset registry, ingestion runs,
WAL/foreign-key configuration, lock, backup boundary, and read-only dashboard
routing. The final migration history progressed beyond the early 0001-0006
outline to at least 0031. Migration 0031_company_sec_filing_issuers.sql
implemented the corrected many-to-many filing/issuer projection and supporting
logic; recover its exact view/trigger/DDL form rather than assuming a physical
join table from memory.

### 18.2 Exact implemented index manifest

The nine directly supported FMP index instruments were:

1. ^GSPC — S&P 500
2. ^N225 — Nikkei 225
3. ^IXIC — Nasdaq Composite
4. ^FTSE — FTSE 100
5. ^DJI — Dow Jones Industrial Average
6. ^STOXX50E — Euro Stoxx 50
7. ^HSI — Hang Seng
8. ^RUT — Russell 2000
9. ^VIX — Cboe Volatility Index

MOVE was desired but blocked by the available FMP subscription and was not one
of the nine implemented OHLC instruments. It belongs in a future licensed
scalar-series adapter if the source supplies only daily levels. ETF proxies must
remain asset_type=etf and must never be relabeled as native indexes.

Historical index coverage included ^GSPC from 1927, ^N225 from 1949, ^IXIC from
1971, and the remaining implemented series from their provider histories.
Provider revisions mean coverage should be audited, not forced to old counts.

### 18.3 Final provider and credential map

| Source | Implemented data | Environment/identity |
| --- | --- | --- |
| Financial Modeling Prep | Economic calendar; equity/ETF/index OHLCV; Treasury curves; profiles/classifications; consensus, earnings, guidance and corporate actions; stock/global news and press releases | FMP_API_KEY |
| SEC EDGAR | CIK/ticker discovery, submissions, filings/accessions, CompanyFacts/XBRL and normalized fundamentals | SEC_USER_AGENT identifying app/contact; no API key |
| Alpaca | Option contracts, snapshots, OI/closes/bars where entitled, underlying quotes; Alpaca/Benzinga news | ALPACA_API_KEY_ID, ALPACA_API_SECRET_KEY, explicit paper/live environment |
| BEA | Official GDP/GDI vintage workbook/archive and curated national accounts | BEA_API_KEY for API datasets; workbook keyless |
| FRED/ALFRED | Macro series and historical vintages; GDPC1 was the historical GDP supplement | FRED_API_KEY for API |
| Philadelphia Fed RTDSM | PCPI and EMPLOY real-time workbooks | Keyless |
| BLS | CPI, PPI, labor, wage and productivity official series/corrections | Public source |
| BIS | Credit-to-GDP, total/private credit and debt-service ratios | Public SDMX |
| Chicago Fed | NFCI, ANFCI and components | Public source |
| New York Fed | SOMA summary, rates/repo facilities and CMDI | Public source |
| Treasury FiscalData | TGA and fiscal funding series | Public source |
| EIA | Electricity plus weekly petroleum/natural-gas and monthly/annual energy facts | EIA_API_KEY |
| NBER chronology | Completed U.S. recession periods | Public chronology; retrospective only |
| Official RSS/Atom | Fed, BEA, Bank of Canada, Statistics Canada, ECB, White House, EIA and State advisories | Keyless |

GDP provider identity is therefore not unknown. The rebuild should use the BEA
workbook/archive as canonical for reported advance/second/third stages. ALFRED
GDPC1 historically extended archived snapshots to 1991-12-04 and reference
periods to 1947Q2, but a later audit found stale/duplicate early GDP data.
Quarantine and re-audit that supplement before reuse. FRED/ALFRED remains valid
for other versioned macro series.

Date-only RTDSM and similar values remain source-native/date-only. Do not invent
a UTC instant. Real timestamp fields should retain an offset-bearing canonical
value and raw source representation.

### 18.4 Company and SEC implementation

The company domain was implemented, not merely planned:

- 518 current securities resolved to 515 current SEC issuers.
- 516 issuer requests included the XOM predecessor CIK.
- Historical baseline: 12,752,398 raw SEC fact versions, 1,374,394 normalized
  fundamental versions, 572,815 filings, and company_data.sqlite around
  30.30 GB.
- S&P scope: 503 securities/500 issuers/500 CompanyFacts/498 normalized
  fundamentals through lineage.
- Dow scope: 30/30/30/30.
- Nasdaq scope: 102 securities/101 issuers/101 CompanyFacts/100 normalized.
- SPCX, FDXF, and HONA lacked financial-statement concepts in the captured
  CompanyFacts. XOM current CIK was sparse, so predecessor lineage supplied
  historical facts.
- Corporate actions, share counts, earnings events, immutable consensus,
  guidance, revisions and earnings-setup projections were implemented.
- Exact latest views included company_consensus_latest and
  company_earnings_events_latest.
- Exact tools and batch contracts are already listed in Section 9.

The current-universe artifacts were
universes/sp500_dow30_nasdaq100.csv, its manifest, and dated FMP constituent
payloads. They were current snapshots, not survivorship-safe membership
history. Never alias FDXF to FDX or HONA to HON. BF-B and BRK-B used the
provider-specific dot-form translations BF.B and BRK.B where required.

### 18.5 Options implementation

Alpaca options were implemented in data/market_data.sqlite.

- Migration 0029_alpaca_options.sql created seven option tables.
- Migration 0030_option_surface_inputs.sql added synchronized surface inputs.
- Exact known table/view name: option_surface_snapshots.
- Universe: SPY, QQQ, IWM, DIA and the 11 Select Sector SPDR ETFs.
- Target DTEs: 1, 2, 3, 7, 14, 30, 60, 90, 180 and 365.
- Each target mapped to the nearest positive listed expiry; duplicate expiry
  mappings were captured once.
- Moneyness grid: 0.80 through 1.20 times spot, dense near ATM, calls and puts.
- Adjusted and nonstandard deliverables were excluded from the standard surface.
- Stored contracts; immutable quote/trade/IV/Greek snapshots; dated open
  interest and close; explicit missing snapshots; historical option-bar
  capability; underlying quote/trade; rates; dividends; and parity-derived
  expiry forwards.
- No acquisition-time liquidity/staleness filter and no internally recomputed
  IV. Those belonged to read-only downstream diagnostics/screens.
- Paper credentials resolved options to Alpaca indicative because OPRA returned
  403. Underlying stock data fell back SIP to IEX. Indicative is derived and
  non-consolidated, must be labeled non-executable, and must never be mixed with
  OPRA in one capture.
- The scheduled bundle accumulated immutable surfaces. Historical option bars
  were later removed from that bundle because entitlement returned OPRA 403 and
  the bars table remained empty.

One historical full-universe checkpoint reported 31,866 contracts, 2,718
surface rows, 2,698 priced rows, 20 explicitly missing rows, 21,589 closes,
19,588 open-interest rows and 96 expiry-input rows.

The recovered read-only tools were options.search_captures,
options.search_contracts, options.get_surface_snapshot,
options.surface_diagnostics, options.screen_contracts,
options.strategy_scenario and research.liquidity_credit_state. They enforced
one capture/feed/underlying/environment, point-in-time eligibility, exact
deliverable verification, explicit volume timeframe and an exclusion ledger.

### 18.6 News and regulatory-event implementation

News used data/news_data.sqlite and was implemented with immutable logical
items, changed-content versions, raw artifacts, capture snapshots, labels and
FTS5 search.

Active/recovered source families:

- FMP stock news.
- FMP press releases.
- FMP global/general news.
- Alpaca/Benzinga company news.
- Official RSS/Atom agency, central-bank, energy, policy and geopolitical feeds.
- Later bounded HTML listing support for approved targets.

The saved core_news_coverage policy covered the resolved 518-stock universe plus
macro, central-bank, energy, policy and geopolitical targets. Company providers
ran in bounded batches; successful target batches committed independently.
Conditional RSS polling distinguished refreshed, unchanged and not due. The
hourly task stored policy/run/target status and used a dedicated news lock.

Article fields included publisher/source, provider item ID, headline,
source-provided description, headline_or_description, safe/canonical URL,
author/image/language when supplied, raw published/provider-updated values and
precision, available_at, captured_at, retraction, symbol/entity/event/topic/
geography labels, body only when licensed, and a material-content hash.

Deduplication order was source plus stable provider ID, then source plus
canonicalized canonical URL, then a documented timestamp/text fallback. A
later identical fetch created a new raw artifact and snapshot membership but
not a duplicate content version. Material headline, description, timestamp,
URL identity, symbol/event/label or retraction changes appended a version.

Publisher targets and rights:

- NYT Business and World RSS, FT International, CNBC Top News/Business/Economy/
  World, and Bloomberg Markets/Economics/Politics were catalogued and reachable
  at the time, but scheduled archival was disabled until storage rights were
  recorded.
- Reuters used no unofficial feed; require a licensed Reuters delivery product.
- Finviz base, Stocks v=3 and ETF v=4 were supported bounded listing layouts
  sharing source_id finviz:news; their unreviewed rights posture must be
  explicitly reapproved.
- Finviz Market Pulse v=6 lacked proven stable article links/identity and stayed
  disabled.
- FinancialJuice returned a JavaScript shell rather than supported static
  listing rows and stayed restricted/disabled.
- Endpoint access is not permission to retain, display, summarize, embed, or
  use the content with an LLM.

A historical full pull requested all 518 symbols, completed 33/33 company
batches, received 3,187 provider records, and left 3,448 unique logical items
and 4,216 versions with a complete FTS index. This is a validation fixture only.

### 18.7 Installed Windows scheduling and operations

Scheduling was implemented and installed; it is not deferred functionality.

| Windows task | Internal job | Final remembered cadence |
| --- | --- | --- |
| QuantData-NewsHourly | news-hourly | Hourly at :10 |
| QuantData-SecDaily | sec-daily | Daily 07:15 |
| QuantData-OptionsClose | options-close | Weekdays 13:20 and 16:20, Alpaca/OPRA-calendar gated |
| QuantData-MacroDaily | macro-daily | Daily 18:00; calendar/funding and official Chicago Fed, BLS, BEA, SOMA and EIA checks with if-new behavior |
| QuantData-MarketClose | market-close | Daily/weekday 18:00 for current equities, ETFs and indexes |
| QuantData-Expectations | expectations | Weekdays 20:00 |
| QuantData-CompanyWeekly | company-weekly | Saturday 09:00 |
| QuantData-MacroMonthly | macro-monthly | Sunday 11:00 freshness check for slower BIS, RTDSM, EIA monthly/annual and GDP-vintage inputs |

The cadence evolved. The original 06:15 Morning job and separate MacroWeekly job
were removed or merged after same-day release latency was identified. Reconcile
the final Task Scheduler XML with this table before reinstalling.

Runtime invariants:

- Windows Task Scheduler invoked WSL through
  ops/run_scheduled_collector.sh and PowerShell installation tooling.
- Credentials came from the private environment without entering command
  output, logs or database rows.
- Canonical per-job/per-database locks and IgnoreNew behavior prevented overlap.
- Network fetches occurred outside transactions; writes were short and
  idempotent.
- Unchanged official releases under if-new produced zero data/artifact/run
  writes.
- Independent steps could continue after one failure, but the aggregate command
  returned nonzero/incomplete. Per-symbol failures must never exit zero.
- Retry only transient failed items; honor Retry-After; authentication/schema
  failures alert rather than loop indefinitely.
- Use SQLite online backup or .backup while WAL is active, not a raw copy of a
  live database. Validate integrity on backup copies.
- Private logs were bounded/retained and never included secrets.

### 18.8 Algorithms page and backtest engines

The /algorithms page was the umbrella research workspace. Standalone Sector
Rotation and Cross-Asset Momentum tabs were removed, while their engines, CLIs
and APIs remained. Old page routes redirected to Algorithms.

Sector rotation final contract:

- All 11 Select Sector SPDR ETFs.
- Price returns only, not total returns.
- Daily rolling 63/126/252-session returns.
- Cross-sectional aggregate rank; equal-weight top three.
- Decide after close t and transact at next common session close.
- Drift-adjusted daily turnover and explicit costs.
- SPX price-index benchmark plus equal-weight 11-sector comparator.
- No missing-price fill.

Cross-asset research used SPY, EFA, EEM, IEF, TLT, TIP, LQD, HYG, GLD, DBC and
BIL. Unlike sector rotation, it used verified distribution-adjusted/total-return
series because bond, credit and cash income is economically material.

Implemented/challenger families included 21-day normalized momentum, 10/40 EWMA
trend, five staggered sleeves, 5-day and 10-day time-series and cross-sectional
momentum/reversal, continuous SPX direction/volatility-level/volatility-slope/
breadth regimes, an expanding regime-interaction ridge, and a low-dimensional
HMM using filtered probabilities only. Smoothed/Viterbi full-sample states were
not valid trading inputs.

The page had a 14-algorithm dropdown, equity curves, SPY total-return and SPX
price benchmarks, leaderboard, regime attribution, current holdings,
subperiods, diagnostics, cost sensitivities and warnings. The common historical
OOS example used 2,777 daily folds from 2015-07-09 to 2026-07-28, next-close
execution and 5-bp costs. None of the tested strategies beat SPY in that sample;
this is an honesty/reconstruction fixture, not a performance promise.

Backtest requirements include training-only transforms/fits, purge/embargo,
explicit decision/execution/outcome times, gross/net attribution, costs,
turnover, benchmarks, exclusions, deterministic ties and strict no-fill rules.

### 18.9 Charts, technical indicators and shared research

The /charts page and associated research system were implemented after the
earlier portal plan. The local portal remained custom static HTML, vanilla
JavaScript and CSS served by QuantDataDashboard/0.1; it was not an embedded
TradingView widget or Pine runtime.

Final navigation was:

- Overview
- Algorithms
- Charts
- GDP vintages
- Tables
- Agent tools

The indicator calculation source was consolidated in
quant_data/technical_indicators.py. Thin adapters in market_tools,
agent_tool_extensions, HTTP and the UI consumed this one implementation.
Unknown keys were rejected, warm-up values remained null with missing_reason,
and response-size bounds were derived before computation.

Calculation/UI catalog:

- SMA, EMA and WMA.
- RSI 14 with chart bounds 30/70.
- MACD 12/26/9.
- PSAR 0.02/0.02/0.2 rendered as blue dots.
- Supertrend ATR 10, hl2, multiplier 3.0, Wilder ATR by default with SMA true
  range alternative; broken green/red bands and transition labels.
- Provider/session and rolling VWAP.
- Autocorrelation Oscillator.
- Squeeze Momentum LazyBear, BB 20/2.0 and KC 20/1.5.
- KDJ 9/3 with Pine-compatible BCWSMA.
- WaveTrend LazyBear 10/21 with ±60/±53 levels and WT2 SMA4.
- CM Ultimate MACD MTF with explicit stored-resolution limitation.
- Williams Vix Fix 22/20/2.0/50/0.85/1.01.
- Smart Money Concepts with internal/swing BOS/CHoCH, EQH/EQL, order blocks,
  FVG and premium/equilibrium/discount only where the backend emitted them.
- swing_structure_forecast with origin-dated support/resistance, target/zone
  and Fibonacci metadata.
- LuxAlgo-style three-cluster SuperTrend AI.

Do not claim proprietary Machine Learning SMC or Volume Bubbles as implemented.
Preserve CC BY-NC-SA attribution and review commercial restrictions for
licensed/reference Pine ports. Daily data cannot reproduce unavailable
intraday/multi-timeframe inputs and must never fake them.

Charts behavior included draggable/zoomable candlesticks, timestamp-aligned
overlays, synchronized lower panes, symbol/indicator/horizon/strategy/metric
controls, buy/sell/short/cover markers, evaluation tables, histograms,
robustness matrices and signal-strength/duration evidence. Warm-ups and missing
values rendered as gaps, not zero.

The shared indicator-research layer separated indicator-specific
features/states/events from:

- Decide after close t, enter at next session open, and exit at declared
  5/10/20-session close.
- Executable and predictive log return.
- Log-price slope, R-squared and slope/realized volatility.
- MFE, MAE, intrawindow max drawdown, realized/downside volatility, path
  efficiency, positive rate, tails/expected shortfall and benchmark adjustment.
- Session-index HAC/Newey-West.
- Moving date-block bootstrap.
- Holm and Benjamini-Hochberg multiplicity corrections.
- Early/middle/late periods, frozen parameter variants and
  SPY/EFA/EEM/TLT/GLD robustness.
- Long-only and long/short simulation, equity/positions/trades/markers.
- Strict JSON artifacts under research_results.

Registered studies were completed for EMA 10/20/50/100, PSAR, Supertrend, RSI,
MACD, Squeeze Momentum and Williams Vix Fix. KDJ, WaveTrend, SMC and Swing
Forecast had calculation/chart support; recover their exact research status
before calling them registered studies.

The WVF reusable strength/duration/extrema study used fixed causal percentile
buckets, duration buckets, bottom proximity/time-to-low labels, contrasts and
monotonicity. Its historical finding was that taller bars indicated wider
future paths, not a reliably completed bottom.

Recovered API memory includes /api/price-series,
/api/indicator-research/strategy-dashboard,
/api/indicator-research/strategy-robustness and /api/agent-tools/call.
The exact final route registry must be recovered from tests.

### 18.10 Local portal and security

Implemented pages were Overview, Algorithms, Charts, GDP vintages, Tables and
Agent Tools. The table inspector used a server-owned allowlist, bound values,
sorting, pagination and URL-persistent state; internal/raw payload tables were
hidden. GDP filters included source, stage, provenance, reference-quarter and
vintage ranges plus revision deltas. Agent Tools used guided manifest forms,
human-readable summaries/tables, loading/warnings/errors, chaining and
expandable/copyable strict JSON with a bounded visual preview.

Live security headers recovered from the old process were:

- Cache-Control: no-store
- Content-Security-Policy: default/style/script/connect from self, images from
  self or data
- Referrer-Policy: no-referrer
- X-Content-Type-Options: nosniff
- X-Frame-Options: DENY

Remembered endpoints include /, /algorithms, /charts, /gdp-vintages,
/table-inspector, /agent-tools, /api/health, /api/price-series,
/api/agent-tools, /api/agent-tools/call and the algorithm/indicator/table/GDP
APIs. Recover route tests before freezing the inventory.

### 18.11 Quant Data Atlas

sites/quant-data-atlas was a separate private static snapshot/exploration
surface, not the canonical local portal and not a live database client.

Recovered hosted site:

https://quant-data-atlas.yanggainan.chatgpt.site

It was private/authenticated to the owner. The published snapshot was dated
2026-07-21. It packaged complete remembered macro/GDP/Treasury/SOMA datasets,
while prices and calendar were capped to the latest roughly 5,000 rows.

Atlas read public/data/manifest.json and chunked dataset JSON. The exporter
supported explicit split market/macro database paths plus a legacy unified
path, read stores in mode=ro/query_only, and mapped each dataset to its owner.
Remembered exports covered 13 datasets across calendar, instruments,
classifications, prices, GDP, macro observations/versions, Treasury, SOMA and
EIA. The UI supported dataset/schema metadata, filtering, search, pagination,
stable column sorting and snapshot freshness.

A recovered Windows draft records Sites project
appgprj_6a5fa1241a688191a3da63d3c490fa0d, deployed source commit
8feea8ac947a987bac2ffc29cfa953096901acf2, and a dependency set including
Next.js 16.2.6, React 19.2.6, TypeScript 5.9.3, Vinext, Vite 8, Tailwind 4,
Drizzle and Wrangler. Only the surviving cache proves Vite; verify the Sites
source archive/package lock before treating these versions as authoritative.

Former publication was not atomically consistent across stores. snapshotAt was
export time, not one unified database as-of. A safe rebuild requires
collector-completion receipts, dataset ownership, a non-overlap lock, explicit
read-only copies, a new exact staging directory, complete manifest/chunk/schema/
freshness validation, semantic-change detection, atomic promotion, one exact
source revision, private deployment with short-lived credentials and a recorded
receipt.

Never recursively clean an empty path, dot, repository root, home directory or
broad volume. Resolve and prove the exact absolute staging target is inside its
designated parent before any cleanup.

### 18.12 Updated historical baselines

These numbers help detect gross reconstruction errors but must not be forced:

| Domain | Historical checkpoint |
| --- | --- |
| Market | 13,465,584 price rows, 2,752 instruments, 2,400 classifications; roughly 2,400 equities, 343 ETFs and 9 indexes |
| Economic calendar | 260,230 FMP events, 2013-01-02 through 2026-07-20 |
| GDP | 87,679 records over 316 reference quarters; archive from 1991-12-04; BEA stage labels from 2002Q1 |
| Treasury | 9,143 dates, 1990-01-02 through 2026-07-20, 12 nullable tenors |
| RTDSM | EMPLOY 12,307 rows and PCPI 2,883 rows through May 2026 observations |
| SOMA | 1,202 weekly dates from 2003-07-09 and 10,818 summary-component rows |
| Funding/liquidity | 62 series and 124,530 immutable observations |
| SEC/company | 12,752,398 raw fact versions, 1,374,394 normalized versions and 572,815 filings |
| Options | 15-ETF universe with prospective immutable surfaces |
| News | Initial full pull left 3,448 logical items and 4,216 versions |

Test counts came from different milestones/branches and are not additive:
800 passed after Algorithms/navigation cleanup, 832 plus 614 subtests after the
later Charts dialog work, and 1,137 plus 1,196 subtests after the shared
indicator-research work. The latest broader remembered validation is the last
of those, but the rebuilt suite must be judged by restored scope, not a magic
count.

### 18.13 Corrected recovery checkpoints and deferred scope

The following Section 15 items are resolved and should no longer be treated as
unknown:

- The exact nine FMP indexes are listed in 18.2; MOVE was not among them.
- GDP sources and stage handling are documented in 18.3.
- Options provider/domain is Alpaca in market_data.sqlite.
- News providers/domain are FMP, Alpaca/Benzinga, RSS/Atom and reviewed bounded
  HTML sources in news_data.sqlite.
- company_data.sqlite and news_data.sqlite are exact implemented filenames.
- Atlas is a separate export/hosting surface.
- Later tests and installed scheduling are documented above.

Still unknown and requiring recovered code/evidence:

- Complete source tree and every post-0031 migration/checksum.
- Exact final schema, index, view and trigger definitions.
- Exact options table names other than option_surface_snapshots.
- Exact final Task Scheduler XML after cadence changes.
- Exact final core_news_coverage JSON/version/hash and rights approvals.
- Full 57-tool argument/result schemas.
- Survivorship-safe historical memberships/classifications.
- Uncommitted UI/indicator work and licensed attachment versions.
- Atlas source/package lock and the last deployment receipt.
- Any external backup containing databases newer than the Atlas snapshot.

Scheduling, news, company, options, Charts and Algorithms are restored scope,
not deferred. The genuinely deferred items remain portfolio/order/execution
management, automatic FX conversion, intraday/tick infrastructure, curve
interpolation, CUSIP-level SOMA holdings, hidden imputation/composite scores,
Phillips-Perron until a validated dependency is chosen, and public/multi-user
exposure of the local portal.

### 18.14 Final rebuild order and acceptance additions

Rebuild in this order:

1. Recover/version-control manifests, pyproject/config, the complete migration
   ledger and tests.
2. Initialize and validate all split-store foundations with temporary paths.
3. Restore canonical identities, artifacts/runs/quality contracts and provider
   adapters.
4. Restore market/calendar, then official macro/vintages/funding/EIA.
5. Restore SEC/company, news and options.
6. Restore the exact 57-tool manifest and HTTP/in-process parity.
7. Restore local Overview, GDP, Tables and Agent Tools.
8. Restore Charts and shared indicator research.
9. Restore Algorithms and walk-forward reports.
10. Restore scheduler definitions one job at a time.
11. Restore Atlas with staged atomic publication.
12. Repopulate only into new non-production stores, then promote after
    idempotence, point-in-time, integrity and backup drills pass.

Additional acceptance gates:

- Every applied migration checksum is recovered or deliberately replaced by a
  new reviewed migration; do not edit applied SQL.
- Four split stores initialize independently; legacy unified compatibility is
  tested or explicitly retired.
- Temp tests cannot fall back to live/default paths.
- Changed/unchanged/partial/error/tombstone semantics pass fixtures.
- Date-only and offset-aware contracts do not fabricate precision.
- No price variant, provider feed, identifier/currency segment, or option
  capture is silently mixed.
- Restricted news targets cannot be enabled without an approved rights record.
- All 57 tools are bounded, deterministic, read-only and strict JSON.
- Indicator golden vectors, causal timing, warm-ups, range invariance,
  boundary resets and overlay/pane UI contracts pass.
- Walk-forward transforms/models are training-only; HMM output is filtered.
- Retired strategy routes redirect to Algorithms.
- Scheduler paths, locks, exit codes, calendars, if-new gates and private logs
  pass dry runs.
- Atlas exports from explicit read-only stores into exact staging, validates all
  chunks and promotes atomically.
- Source, secrets, each SQLite database/WAL state, Task Scheduler definitions
  and Atlas metadata have a tested backup/restore procedure.

Where recovered executable artifacts conflict with this document, validated
source, migrations and database evidence take precedence. Record the conflict
and resolution in this plan rather than silently rewriting history.


## 19. Charts UI reconstruction erratum

This section closes the remaining presentation details omitted from Section
18.9 and is part of the restored Charts acceptance contract.

### 19.1 Indicator picker and right-panel layout

The indicator picker was a viewport-bound dialog, not an unconstrained page.

- Desktop used a two-column body: indicator catalog on the left and the selected
  indicator editor on the right.
- The shell kept an approximately 28-pixel viewport margin and used grid rows
  equivalent to auto / minmax(0, 1fr) / auto.
- The body height was bounded at approximately min(74dvh, 760px).
- The editor had min-height: 0 and independent overflow-y scrolling.
- The Add to chart action stayed in a sticky bottom action area and remained
  reachable regardless of the editor form length.
- At widths of 760 pixels or less, the body became one column, its height became
  automatic, and the overall dialog/page scrolled so the Add button could never
  be trapped below an inaccessible fixed panel.
- Visual treatment: off-white/cream dialog over the dark chart, pale-mint
  selected cards, forest-green primary action, colored indicator dots, rounded
  controls, serif display headings and uppercase letter-spaced sans labels.
- Form validation had to accept exact configured numeric steps, including the
  Squeeze Momentum multiplier 2.0.

Required responsive tests must prove that the action remains reachable on
desktop and mobile, the right editor scrolls independently on desktop, and no
fixed-height ancestor clips the form.

### 19.2 Price overlays versus independent lower panes

Indicator outputs were aligned by timestamp, never by array index. Warm-up and
missing values were gaps rather than zeros.

**Price-pane overlays sharing the candlestick price scale:**

- EMA, SMA and WMA with user-configurable period lists. Common visual reference
  periods included 20, 50, 100 and 200; the registered EMA research state used
  10, 20, 50 and 100.
- Supertrend.
- PSAR.
- Provider/session VWAP and configurable rolling VWAP.
- Smart Money Concepts.
- swing_structure_forecast.
- LuxAlgo-style SuperTrend AI.

**Independent synchronized lower panes:**

- Volume.
- RSI.
- MACD.
- Autocorrelation Oscillator.
- Squeeze Momentum.
- KDJ.
- CM Ultimate MACD MTF.
- Williams Vix Fix.
- WaveTrend LazyBear.

Volume is therefore a first-class lower pane even though it is market input
rather than a derived indicator calculation.

### 19.3 Exact overlay presentation contracts

**Supertrend**

- Broken 2-pixel green uptrend and red downtrend lines; never one connected
  pink line.
- Transition circles at changes.
- Green Buy label-up and red Sell label-down markers with white text.
- Translucent green/red regime fills when highlighting is enabled.
- Pine-v4 defaults remained ATR period 10, hl2 source, multiplier 3.0, Wilder
  ATR by default with SMA true-range alternative.

**PSAR**

- Small round #2962FF dots at the SAR values.
- Never render PSAR as a connected line or cross markers.
- Defaults: start 0.02, increment 0.02, maximum 0.2.

**Smart Money Concepts**

- Thin dashed bullish-teal and bearish-red structure segments.
- Small BOS and CHoCH labels, plus EQH/EQL labels where emitted.
- Low-opacity red/pink upper supply zones and blue lower demand zones extended
  only forward from their origin.
- Weak High, Strong High, Weak Low and Strong Low edge labels/lines where
  provided by the backend.
- Preserve candlestick readability; never cover the price pane with opaque
  blocks.
- Order blocks, fair-value gaps, premium/equilibrium/discount or prior-period
  levels may be shown only when they exist in the backend response contract.

### 19.4 Exact research/UI details retained for parity

- EMA state was bullish when close > EMA10 > EMA20 > EMA50 > EMA100, bearish
  under the exact reverse ordering, and mixed otherwise.
- The WVF strength study used percentile buckets at or below 80, 80-90, 90-95
  and above 95, with stress-duration buckets 1, 2, 3-4, 5-9 and 10+.
  Left-censored episodes must be identified rather than treated as known
  full-duration runs.
- The bounded dashboard research cache was remembered as eight entries with an
  approximately 600-second TTL, database main/WAL fingerprinting and
  single-flight computation. Recover exact constants from source/tests before
  treating them as stable API.


## 20. Database schema and semantic no-write erratum

This section records later exact recovery evidence and supersedes the contrary
statements in Sections 5.2 and 18.13. In particular, the options domain and its
physical table names are no longer unknown. The exact column DDL, constraints,
indexes, SQL text and migration hashes still require recovery from executable
artifacts or a database copy.

### 20.1 Exact implemented options tables

All of these were physical tables in `data/market_data.sqlite`. Migration
`0029_alpaca_options.sql` created the first seven; migration
`0030_option_surface_inputs.sql` added the final six synchronized-input tables.

| Migration | Exact physical table | Recovered responsibility |
| --- | --- | --- |
| 0029 | `option_contracts` | Canonical Alpaca option-contract identity and deliverable metadata. |
| 0029 | `option_open_interest` | Dated contract open-interest observations. |
| 0029 | `option_close_prices` | Dated contract close-price observations. |
| 0029 | `option_surface_captures` | Immutable capture parent, feed/environment, request scope, status and availability boundary. |
| 0029 | `option_capture_underlyings` | Underlying membership and capture-level underlying state. |
| 0029 | `option_surface_snapshots` | Per-contract surface rows, including quote/trade, IV/Greeks and explicit missing state where applicable. |
| 0029 | `option_bars` | Historical option OHLCV capability; the scheduled bundle later disabled this path after OPRA entitlement failures. |
| 0030 | `option_capture_underlying_quotes` | Underlying quote/trade input synchronized to the surface capture. |
| 0030 | `option_capture_rate_curves` | Capture-level rate-curve identity and provenance. |
| 0030 | `option_capture_rate_curve_points` | Tenor/rate points belonging to the capture curve. |
| 0030 | `option_capture_dividend_sets` | Capture-level dividend-input set and provenance. |
| 0030 | `option_capture_dividend_cashflows` | Dated dividend cash flows belonging to a dividend set. |
| 0030 | `option_capture_expiry_inputs` | Per-expiry spot/rate/dividend/forward inputs used by the captured surface. |

The capture contract from Section 18.5 remains binding: one immutable capture,
one underlying, one resolved feed and one environment per surface; indicative and
OPRA observations must never be mixed. Corrections append new captures or dated
observations rather than rewriting a historical surface.

### 20.2 Exact recovered migration sequence

The following is the recovered migration order and semantic map. Preserve the
order during reconstruction. Descriptions are exact recovery scope, not a
substitute for the erased SQL; recreate and fixture-test the DDL before treating
migration parity as complete.

| Migration | Recovered scope |
| --- | --- |
| 0000 | Infrastructure, migration ledger and shared ingestion/provenance foundation. |
| 0001 | Core instrument/economic-calendar schema. |
| 0002 | Market prices and market-domain schema. |
| 0003 | GDP vintages. |
| 0004 | Instrument classifications. |
| 0005 | Treasury yield curves. |
| 0006 | First-generation macro schema. |
| 0007 | Macro v2 revision-aware schema. |
| 0008 | SOMA ingestion/storage. |
| 0009 | EIA electricity retail-sales data. |
| 0010 | SOMA scope correction. |
| 0011 | Summary-only SOMA purge; CUSIP-level holdings were intentionally removed. |
| 0012 | Economic-calendar versions. |
| 0013 | Macro snapshot membership. |
| 0014 | Price correction versions. |
| 0015 | Treasury correction versions. |
| 0016 | GDP source identity. |
| 0017 | EIA page staging. |
| 0018 | Market history registry. |
| 0019 | Macro history registry. |
| 0020 | Price ingestion requests. |
| 0021 | Treasury natural-key guard. |
| 0022 | EIA weekly fundamentals. |
| 0023 | EIA retail point-in-time semantics. |
| 0024 | SEC core: issuers, filings, CompanyFacts and normalized fundamentals. |
| 0025 | Corporate actions and share-count history. |
| 0026 | Corporate-action integrity. |
| 0027 | U.S. recession periods. |
| 0028 | Earnings expectations, consensus and guidance. |
| 0029 | Alpaca options tables and immutable surface captures. |
| 0030 | Synchronized option-surface inputs. |
| 0031 | Filing-issuer derived view and invariants for joint filings. |

Migration `0031` must be rebuilt as the recovered derived filing-issuer
view/supporting invariants and triggers, not guessed as a physical many-to-many
join table. Applied migration SQL is immutable; any reconstruction correction
must use a new migration rather than mutating an applied file.

### 20.3 Exact daily semantic no-write gates

Daily polling was release-driven. A network request may occur every day, but the
collector must decide semantic identity before opening a write run or persisting
an artifact.

- **BLS:** canonical response identity ignores only the top-level
  `responseTime`; parsed observations, source messages and request scope remain
  identity-bearing.
- **BEA:** compare the canonical parsed-table identity and exclude
  `UTCProductionTime`, which can change between identical requests.
- **SOMA:** compare a deterministic identity of the latest summary-only release;
  do not restore deleted CUSIP-level holdings as part of this gate.
- **EIA weekly:** compare the set of `(series_id, period, content_sha256)`
  identities for the requested scope.
- **Chicago Fed:** compare the parsed observation set together with the
  artifact/request scope, so a changed scope cannot masquerade as unchanged
  data.

For every gate, unchanged means **zero** ingestion-run, raw-artifact, snapshot
or snapshot-membership writes. New or corrected semantic content appends through
the normal immutable/version-aware path. Partial responses, errors, timeouts and
HTTP 429s are failures or retryable states, never evidence that a release is
unchanged and never grounds for tombstones.

### 20.4 Recovery interpretation

This section specifically supersedes the Section 18.13 bullet saying exact
option table names other than `option_surface_snapshots` were unknown. The 13
names above and the `0000`-`0031` semantic order are recovered facts. What remains
unknown is the byte-exact DDL: individual columns not otherwise documented,
constraints, indexes, views/triggers beyond the recovered contracts, SQL file
hashes and migration checksums. Restore those from surviving database copies or
executable artifacts when available; do not invent them merely to match the
names.

### 20.5 Exact recovered physical table inventory

The names below are recovered physical tables unless explicitly labeled as a
view. They supersede proposed or differently named logical entities elsewhere
in this document. Do not infer whether an unlisted object was a table or view
without recovered SQL.

**Shared physical tables in each domain store**

- `schema_migrations`
- `dataset_registry`
- `ingestion_runs`

**Market physical tables**

- `instruments`
- `instrument_identifiers`
- `instrument_classifications`
- `prices_daily`
- `prices_daily_versions`
- `market_price_ingestion_requests`
- `option_contracts`
- `option_open_interest`
- `option_close_prices`
- `option_surface_captures`
- `option_capture_underlyings`
- `option_surface_snapshots`
- `option_bars`
- `option_capture_underlying_quotes`
- `option_capture_rate_curves`
- `option_capture_rate_curve_points`
- `option_capture_dividend_sets`
- `option_capture_dividend_cashflows`
- `option_capture_expiry_inputs`

**Macro physical tables**

- `economic_calendar`
- `economic_calendar_event_versions`
- `us_recession_periods`
- `gdp_vintages`
- `treasury_yield_curves`
- `treasury_yield_curve_versions`
- `macro_series`
- `macro_releases`
- `macro_observations`
- `macro_series_dimensions`
- `macro_source_artifacts`
- `macro_source_snapshots`
- `macro_observation_versions`
- `macro_snapshot_scopes`
- `macro_snapshot_observation_membership`
- `soma_snapshots`
- `soma_source_artifacts`
- `soma_snapshot_artifacts`
- `soma_summary_components`
- `eia_electricity_source_snapshots`
- `eia_electricity_source_artifacts`
- `eia_electricity_ingestion_pages`
- `eia_electricity_snapshot_artifacts`
- `eia_electricity_retail_sales_versions`
- `eia_electricity_snapshot_versions`
- `eia_electricity_snapshot_scopes`
- `eia_electricity_snapshot_membership`
- `eia_weekly_source_snapshots`
- `eia_weekly_source_artifacts`
- `eia_weekly_snapshot_artifacts`
- `eia_weekly_fundamental_versions`
- `eia_weekly_snapshot_versions`

**Company physical tables**

- `company_sec_artifacts`
- `company_issuers`
- `company_sec_snapshots`
- `company_issuer_versions`
- `company_ticker_snapshot_membership`
- `company_issuer_security_link_assertions`
- `company_sec_filings`
- `company_sec_filing_snapshot_membership`
- `company_sec_fact_versions`
- `company_sec_fact_snapshot_membership`
- `company_metric_definitions`
- `company_metric_mappings`
- `company_fundamental_observation_versions`
- `company_action_source_artifacts`
- `company_action_snapshots`
- `company_corporate_action_versions`
- `company_action_snapshot_membership`
- `company_expectation_metric_definitions`
- `company_earnings_source_artifacts`
- `company_earnings_source_snapshots`
- `company_consensus_observation_versions`
- `company_consensus_snapshot_membership`
- `company_earnings_event_versions`
- `company_earnings_event_snapshot_membership`
- `company_guidance_versions`

**Confirmed company view and migration 0031 invariants**

`company_sec_filing_issuer_membership` was a view, not a physical join table. It
derived distinct `(accession_number, issuer_id)` pairs from immutable submissions
snapshots plus `company_sec_filing_snapshot_membership`. Migration 0031 also:

- renamed `company_sec_filings.issuer_id` to
  `company_sec_filings.first_observed_issuer_id`;
- added `idx_company_sec_filing_membership_accession`; and
- installed issuer-scope, first-observed and guidance-accession validity
  triggers.

## 21. Dated clarification: Stage 12 canonical market path

Decision date: 2026-08-15

Historical references in this recovery document to `data/market_data.sqlite`
remain evidence of the earlier design and are not rewritten. The explicit user
decision and accepted [ADR 0009](docs/adr/0009-canonical-market-operational-path.md)
now designate `data/market.sqlite` as the sole current project-relative market
default. Exact historical registry projections may retain the former value only
to reproduce accepted Stage 1–11 evidence.

This clarification changes no existing file, candidate, or receipt and
authorizes no database access, copy, move, population, promotion, provider
request, public exposure, or scheduler action. Bounded offline Stage 12A freezes
Market v1 authority, its explicit retained roster and coverage/non-claims, and
the later serial lifecycle gates. Stages 12B through 12E require separate
authorization.
