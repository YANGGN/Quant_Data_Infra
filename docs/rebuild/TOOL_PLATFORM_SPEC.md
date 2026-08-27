# Tool Platform Specification

Status: Accepted

## Purpose

This specification defines the accepted read-only analytical tool platform for
the rebuilt Quant Data Infrastructure. It separates the stable public tool
surface from the smaller set of typed query, transformation, statistical, and
research primitives that implement it.

The exact 57 recovered public names are the accepted eventual compatibility
target, not evidence that the rebuilt platform has been implemented. Those
names and their versioned request and response contracts MUST be
preserved. They MUST NOT be rebuilt as 57 independent copies of storage,
validation, time-series, or statistical logic.

Related documents:

- [Architecture](../../ARCHITECTURE.md)
- [System registry specification](SYSTEM_REGISTRY_SPEC.md)
- [Data and time contracts](DATA_AND_TIME_CONTRACTS.md)
- [Roadmap](../../ROADMAP.md)
- [ADR 0005: Composable tool core](../adr/0005-composable-tool-core.md)

## Scope

This document specifies:

- the accepted 57-name compatibility surface and additive native tools;
- the internal typed primitive boundary;
- registry, schema generation, and validation requirements;
- host-controlled read-only store routing;
- in-process and JSON composability;
- resource and concurrency bounds;
- deterministic strict-JSON behavior;
- observability, versioning, compatibility, and deprecation policy; and
- offline acceptance evidence required before the platform is called restored.

It does not specify collectors, schema migrations, arbitrary SQL access,
browser-triggered ingestion, or deployment.

## Normative language

The terms MUST, MUST NOT, SHOULD, SHOULD NOT, and MAY are normative. A public
contract is not implemented merely because it appears in this document. Its
status changes only after the acceptance evidence in this specification is
recorded.

## Compatibility decision

The user accepted 57-name compatibility on 2026-08-09. All names in this
document are reserved under API version `1.0`, subject to recovered contract
fixtures and the versioning rules below. The original Stage 1 milestone
validated only `macro.get_series` and `timeseries.describe`; that historical
registry was explicitly a milestone subset and did not claim full 57-tool
restoration or reuse any reserved name with different semantics.

Registry `2.41.0` adds `market.get_price_series` as one native, additive
API `1.0` tool outside that recovered compatibility set. Registry `2.42.0`
adds `market.get_available_ticker` as a second native additive tool. Registry
`2.46.0` adds `market.get_volume_series` and
`macro.get_release_calendar` as the third and fourth native additive tools and
adds explicit `2.0.0` variants for the three existing macro catalog/series
names. The reserved compatibility inventory remains exactly 57 names; the
current active manifest builds on that increment. Registry `2.47.0` adds four
more native tools: `stats.distribution_diagnostics`,
`stats.covariance_matrix`, `stats.bootstrap_confidence_interval`, and
`stats.principal_components`. It also adds explicit `2.0.0` successors for
the reserved `data.quality_audit` and `timeseries.transform` names. The current
manifest therefore has 65 logical names. None of the eight additive tools
claims recovered behavior, and the two successors do not mutate their frozen
v1 contracts. Registry `2.48.0` additionally provides an explicit `2.0.0`
successor for reserved `market.technical_indicators`; the logical-name count
therefore remains 65 and the frozen v1 contract is unchanged.

The recovered compatibility names are:

| Family | Count | Public names |
| --- | ---: | --- |
| Macro | 12 | `macro.search_series`, `macro.describe_series`, `macro.get_series`, `macro.get_intraday_releases`, `macro.release_surprises`, `macro.revision_analysis`, `macro.align_us_recessions`, `macro.standardize_surprises`, `macro.get_liquidity_snapshot`, `macro.get_liquidity_impulse`, `macro.get_credit_conditions`, `macro.regime_snapshot` |
| Time series | 4 | `timeseries.transform`, `timeseries.describe`, `timeseries.align`, `timeseries.correlation` |
| Econometrics | 5 | `econometrics.regression`, `econometrics.stationarity`, `econometrics.rolling_regression`, `econometrics.structural_breaks`, `econometrics.local_projection` |
| Company | 10 | `company.search_issuers`, `company.search_filings`, `company.get_fundamentals`, `company.get_corporate_actions`, `company.get_share_count_history`, `company.get_earnings_calendar`, `company.get_consensus_history`, `company.get_guidance_history`, `company.get_estimate_revisions`, `company.get_earnings_setup` |
| Energy | 2 | `energy.get_electricity_retail_sales`, `energy.get_weekly_fundamentals` |
| Market | 5 | `market.search_instruments`, `market.get_returns`, `market.get_forward_returns`, `market.technical_indicators`, `market.cross_sectional_performance` |
| Rates | 3 | `rates.get_funding_conditions`, `rates.get_repo_facility_usage`, `rates.curve_analytics` |
| Options | 6 | `options.search_captures`, `options.search_contracts`, `options.get_surface_snapshot`, `options.surface_diagnostics`, `options.screen_contracts`, `options.strategy_scenario` |
| Research, diagnostics, forecast, and news | 10 | `research.point_in_time_panel`, `data.quality_audit`, `research.event_study`, `alpha.signal_diagnostics`, `research.walk_forward_backtest`, `research.robustness_suite`, `stats.multiple_testing`, `forecast.evaluate`, `news.search`, `research.liquidity_credit_state` |

The current active family counts are macro 13, time series 4, econometrics 5,
company 10, energy 2, market 8, rates 3, options 6, and research 14. Versioned
variants do not create additional logical names.

The additive `market.get_price_series` contract accepts a server-resolved
ticker plus explicit mode, cutoff policy, and limit. `start_date` and
`end_date` are independently optional inclusive trade-date bounds. Its
successful result contains exactly four typed `TimeSeries` values in
`open`, `high`, `low`, `close` order from one coherent Stage 10 row
selection. Prices remain raw provider-native values; adjustment and session-
calendar semantics are explicitly not established. Callers cannot provide a
database path, SQL, provider choice, or writable connection.

The additive `market.get_available_ticker` contract accepts an optional
`limit` only; omission defaults to the 10,000-record hard bound, so `{}` is
the normal full-list request. It returns deterministic typed records for
FMP/provider-native Stage 10 instruments only when a current price pointer,
immutable version, and capture are retrievable through the latest read model.
It is current retained-data discovery, not a live or historical point-in-time
universe. Callers cannot provide dates, an as-of cutoff, a provider, path, SQL,
or writable connection.

The additive `market.get_volume_series` contract uses the same fixed Stage 10
instrument identity and immutable row selection as the verified price reader.
It returns one provider-native daily volume series, permits independently
optional inclusive start/end bounds, preserves zero volume, and explicitly
declares that unit normalization and adjustment semantics are not established.

The explicit `2.0.0` variants of `macro.search_series`,
`macro.describe_series`, and `macro.get_series` read the current canonical
macro catalog. Exact describe lookup is not implemented as a bounded search.
The ten official-vintage identifiers use only the official-vintage model; all
other identifiers use only the generic canonical version core, with no
cross-model fallback. `latest`, `as_of`, and `first_release` selection report
requested and actual mode, cutoff precision, date-only policy, availability
basis, selected versions, truncation, and point-in-time status. First release
requires both the explicit flag and its evidence. An explicitly inclusive
same-day date-only observation at an intraday cutoff is returned as unsafe with
the deterministic safety warning; no timestamp is invented. Every canonical
macro read first reconciles the store's complete macro migration ledger and the
required dataset identity declarations against the reviewed registry.

The additive `macro.get_release_calendar` contract composes immutable migration
0016 wholesale captures with migration 0018 incremental event versions. It
reconciles the exact provider/country/event-time/event-name/currency identity,
ranks exact capture instants first and stored correction sequence before the
version identifier, preserves provider JSON scalars as text, and reports the
complete temporal request audit in deterministic diagnostics. First-release is
not a calendar mode.

The supported cross-project boundary is the fixed
`bin/quant-data-tools` subprocess documented in
[Local Agent Tools](../LOCAL_AGENT_TOOLS.md). It exposes `list`, `describe`,
`manifest`, and strict-JSON stdin/stdout `call`; it derives the project root,
registry, and store routes host-side and starts no URL or service.

The loopback manifest endpoint and call endpoint are `GET /api/agent-tools`
and `POST /api/agent-tools/call`; direct/HTTP parity is executable acceptance
evidence. Cross-project agents on the same computer use the local subprocess
boundary above and require no URL.

## Architectural boundaries

The platform MUST have four layers.

### Public adapters

Public adapters own only:

- public name resolution;
- request-envelope parsing;
- schema validation;
- compatibility defaults and field names;
- conversion between JSON-native values and typed values; and
- conversion of a typed result into the versioned public response.

Adapters MUST NOT contain SQL, provider parsing, indicator formulas,
statistical estimators, availability selection, or return calculations.

### Typed dispatcher

The dispatcher MUST:

- resolve the immutable registry entry for the requested name;
- bind a host-created execution context;
- reject unknown names and arguments before domain execution;
- apply preflight resource bounds;
- invoke exactly one registered operation graph;
- validate the typed result and public output; and
- emit a sanitized execution receipt.

Dispatch MUST fail closed. It MUST NOT discover functions by arbitrary import
path, `eval`, reflection over caller text, or user-supplied SQL.

### Shared primitives

The implementation SHOULD converge on a small primitive catalog rather than a
tool-per-feature architecture. At minimum it MUST define reusable primitives
for:

- store-bound search and description queries;
- point-in-time observation selection;
- immutable `Observation` and `TimeSeries` construction;
- deterministic alignment and transformation;
- return and forward-return target construction;
- descriptive statistics and correlation;
- regression, rolling estimation, stationarity, breaks, and projections;
- event, signal, walk-forward, robustness, multiplicity, and forecast
  evaluation;
- market, macro, company, rates, energy, options, and news projections; and
- lineage, warnings, exclusions, uncertainty, and research-contract assembly.

A public tool MAY compose multiple primitives. Two public tools with the same
semantic operation MUST call the same primitive rather than maintain parallel
implementations.

### Store gateways

Store gateways own fixed, parameterized queries and row-to-domain conversion.
They MUST be separated by logical owner: market, macro, company, and news.
Cross-store analysis MUST read each owner through its gateway and compose only
typed in-memory values. A gateway MUST NOT attach a caller-selected database,
initialize a schema, or write through a read path.

## Core typed contracts

The internal core MUST use immutable typed values. JSON dictionaries are a
boundary representation, not the internal composition API.

### ExecutionContext

The host-created context MUST include:

- request identifier;
- API and registry revisions;
- tool and operation versions;
- resolved market, macro, company, and news store handles;
- an optional host-authorized live-provider capability;
- deadline and cancellation state;
- resource budget;
- deterministic clock/as-of inputs where required; and
- an observability sink that never receives secrets or raw artifacts.

The caller MUST NOT override store paths, credentials, registry revision,
deadlines above host maxima, or execution mode.

### Observation and TimeSeries

An observation MUST preserve:

- `period_start` and `period_end`;
- nullable value with an explicit missing reason when absent;
- `available_at` when evidenced;
- unit and frequency inherited from or checked against the series;
- quality flags;
- source identity and revision/vintage provenance; and
- transformation lineage.

A time series MUST additionally preserve stable series identity, display and
source metadata, timezone/date semantics, ordering, duplicate policy, and a
declared missing-value policy. Transformations MUST return new values and MUST
NOT mutate their input.

### QueryResult and ResearchResult

A query result MUST distinguish records, series, matrices, diagnostics,
warnings, truncation, and lineage. A research result MUST also carry the
versioned research contract, including:

- analysis and model identity;
- parameters;
- decision `as_of`;
- point-in-time status and unsafe reasons;
- vintage, availability, and execution policies;
- sample and exclusions;
- uncertainty; and
- input lineage.

Unsafe research MUST remain callable only when its schema permits it and the
result identifies why safety is not established. The adapter MUST NOT relabel
an ex-post result as point-in-time safe.

## Registry contract

The system registry is the only public-name authority. Each tool entry MUST
contain:

- public name and family;
- API version, tool version, and semantic operation version;
- lifecycle state: proposed, experimental, stable, deprecated, or retired;
- compatibility status and any predecessor name;
- human description, assumptions, and examples;
- input type and generated input-schema identifier;
- output type and generated output-schema identifier;
- operation graph identifier;
- logical stores read;
- whether a host-authorized live-provider capability is possible;
- required point-in-time, availability, and return contracts;
- deterministic resource-cost expression and hard limits;
- timeout class;
- composable input and output types;
- observability classification; and
- owner and review requirements.

The registry MUST be deterministic. A canonical registry digest MUST change
whenever any name, schema, default, limit, operation binding, or semantic
version changes.

No registry entry may declare write access. The sole recovered live exception,
`macro.get_intraday_releases`, MAY be retained only if 57-name compatibility is
accepted. Its capability MUST be host-enabled, non-persistent, bounded to one
Eastern calendar day, and disabled when the required credential is absent. It
MUST NOT write a store or become replay-safe evidence.

## Schema generation and validation

### Single source of truth

Input and output schemas MUST be generated from the same typed definitions used
by the dispatcher. Hand-maintained schema copies are prohibited.

Generated schemas MUST:

- use one pinned JSON Schema dialect;
- have stable `$id` values containing schema name and semantic version;
- declare required fields explicitly;
- set `additionalProperties: false` for every modeled object;
- permit open maps only where the typed contract deliberately declares one;
- distinguish nullable values from omitted values;
- expose enumerations, string formats, numeric bounds, array limits, and
  cross-field constraints where expressible;
- include schema-visible workload limits; and
- avoid accepting a database path, SQL fragment, import path, or credential.

Constraints not expressible in JSON Schema MUST be implemented as named typed
validators and listed in registry metadata.

### Boundary validation

The request MUST be validated in this order:

1. enforce the 8 MiB maximum request body before buffering more data;
2. decode strict UTF-8 JSON and reject duplicate object keys;
3. validate the request envelope;
4. resolve the exact registry name and API version;
5. validate arguments against the generated schema;
6. run named semantic validators and resource preflight; and
7. construct typed values.

The result MUST be validated twice: once as the internal typed result and once
after public adaptation against the generated output schema. An output-schema
failure is an internal error, not a partial successful response.

Validation errors MUST be structured and bounded. They MAY identify a JSON
pointer, rule, and safe message, but MUST NOT include a stack trace, local path,
SQL text, secret, or raw provider body.

## Strict read-only routing

### Host-selected paths

The host MUST resolve all market, macro, company, and news paths before tool
dispatch. Public input MUST NOT select or override a path. The interface MUST
require four distinct split stores. Unified runtime compatibility and partial
path aliasing are retired. If two configured roles resolve to the same physical
file, validation fails before dispatch.

### Connection rules

Every SQLite read connection MUST:

- open an existing file with `mode=ro`;
- set `PRAGMA query_only=ON` and foreign-key checking behavior appropriate to a
  reader;
- avoid WAL, migration, schema-initialization, repair, attach, vacuum, or
  temporary persistent tables;
- use bounded busy and statement timeouts; and
- validate a domain anchor before serving a query.

Composed analyses MUST close or release all store readers deterministically.
When one cutoff must govern several company or cross-store components, the
operation graph MUST bind that cutoff once and preserve each store's observed
snapshot identity. It MUST NOT imply a distributed transaction or a single
cross-store commit time.

### Query safety

Only registered parameterized queries and allowlisted sort/projection tokens
may reach a gateway. Identifier selection MUST come from server-owned maps.
Raw SQL, SQL expressions, arbitrary table names, PRAGMAs, and arbitrary file
reads are prohibited at the public boundary.

## Composability

### In-process composition

Operations MUST accept typed inputs directly. A series fetched by one operation
must be reusable by transform, describe, stationarity, regression, align, and
correlation operations without a lossy JSON encode/decode cycle.

Composition MUST preserve:

- stable semantic IDs;
- period and availability timestamps;
- unit/frequency compatibility;
- missing reasons and quality flags;
- source and transformation lineage;
- return-target contracts; and
- point-in-time safety status.

An operation MUST reject contradictory units, frequencies, return methods,
availability policies, or target definitions. It MUST NOT guess a fiscal link,
fill a missing observation, or silently downsample.

### Public composition

The public JSON representation MAY be supplied as a later tool input only when
the receiving schema names that exact composable type and version. The decoder
MUST revalidate the full typed contract. Arbitrary prior tool responses MUST
not be treated as trusted objects.

Large server-side object handles are out of scope for API version `1.0` unless
a later ADR defines lifetime, authorization, and reproducibility semantics.

## Resource bounds and failure behavior

Every tool MUST have a deterministic preflight cost model based only on
validated inputs and registry constants. It MUST reject a request before an
expensive query or calculation if a hard limit can be proven exceeded.

Recovered ceilings retained by the accepted compatibility decision include:

- 10,000 macro observations per call;
- 20 input series where a multi-series operation permits them;
- 20 technical-indicator specifications, 10,000 returned bars, and 200,000
  indicator output values;
- at most 101 unique correlation lags, absolute lag 252, 25,000 rolling
  estimates, and 5,000,000 lead/lag row evaluations;
- 50 cross-sectional members, 10 horizons, horizon 252, 500 evaluation dates,
  and 100,000 output cells;
- 500 option captures, 2,000 option contracts, and 5,000 contracts in one
  single-capture surface; and
- a bounded limit for every search and page.

The host MUST also enforce bounded concurrent requests, execution deadlines,
result size, recursion/nesting depth, and rendered-preview size. Truncation is
allowed only when the public schema defines it and the result states the cap,
returned count, and whether more data exists. Statistical samples MUST NOT be
silently truncated.

Timeout, cancellation, unavailable store, invalid request, resource limit,
unsupported method, and internal output-validation failure MUST be distinct
error classes. Retrying validation or deterministic resource failures is not
appropriate.

## Deterministic JSON

All public responses MUST be valid RFC JSON.

- `NaN`, positive infinity, and negative infinity MUST be rejected before
  serialization; a domain operation MAY convert an unavailable statistic to
  `null` only when it also emits the declared missing reason or warning.
- Object keys, set-like collections, ties, warnings, and lineage records MUST
  have defined stable ordering.
- Dates and timestamps MUST follow the data and time contracts. Offset-naive
  timestamps are prohibited where an instant is claimed.
- Floats MUST use one deterministic finite-number encoding. Decimal source
  values MUST not pass through an undocumented binary-float conversion.
- Missing, omitted, zero, false, empty, and unchanged MUST remain distinct.
- Canonical test and receipt serialization MUST sort object keys and use a
  fixed UTF-8 representation so identical typed results hash identically.

Responses MUST not expose credentials, local paths, SQL, raw artifacts,
private log locations, or exception traces.

## Observability

Each attempted dispatch MUST produce a bounded sanitized receipt containing:

- request ID;
- public name and version;
- registry revision and schema digests;
- operation graph version;
- start and finish instants and elapsed time;
- outcome class;
- logical stores read and non-sensitive store identity aliases;
- validated workload dimensions and applied limits;
- returned shape and truncation state;
- warning/error codes; and
- lineage or analysis ID when returned by the result.

Receipts and logs MUST NOT contain credentials, raw request bodies, caller
series values, local database paths, raw provider payloads, or unrestricted
query text. High-cardinality user values SHOULD be represented by bounded
counts or salted/ephemeral diagnostic hashes, not durable identifiers.

The platform SHOULD expose aggregate latency, failure, timeout, resource-reject,
and store-unavailable metrics by tool/version. Metrics MUST not change result
semantics.

## Versioning, compatibility, and deprecation

Four versions are independent and MUST be recorded:

- API envelope version;
- public tool semantic version;
- input/output schema version;
- primitive or model version.

An additive optional field is compatible only when recovered fixtures and all
known consumers tolerate it. Changes to defaults, availability selection,
return definition, execution timing, missingness, unit, statistical estimator,
ordering, or truncation are semantic changes even if the JSON shape is
unchanged.

For the accepted 57-name compatibility surface:

- API version `1.0` names MUST keep their recovered meaning;
- compatibility adapters MAY translate old field spellings or envelopes into
  typed inputs, but MUST emit one canonical result contract;
- a changed semantic contract requires a new tool/schema version and explicit
  selection until the deprecation window ends;
- deprecated tools remain discoverable with replacement and removal metadata;
- deprecation warnings MUST be machine-readable and deterministic; and
- a public name MUST NOT be silently rebound to a different primitive meaning.

Registry `2.32.0` applies this rule to the two market-return names. The HTTP
call envelope accepts the legacy three fields or one additional controlled
`tool_version` string. Omitted selection and explicit `1.0.0` resolve the
unchanged legacy result; explicit `2.0.0` resolves the typed Stage 10 contract.
An unknown version fails before argument decoding or store access. Discovery
keeps exactly 57 top-level names and publishes ordered variants, replacement
metadata, and the currently unscheduled removal state. Deprecation warnings
are receipt/manifest metadata and do not mutate the frozen v1 result schema.

Registry `2.33.0` applies the same selection rule to `timeseries.describe`,
`timeseries.align`, and `timeseries.correlation`. Their explicit `2.0.0`
variants consume the series element from a Stage 10 return v2 result, perform
no store access, and require compatible return, point-in-time, unit, frequency,
and registry-revision contracts. A composition-only schema tightens capture
timing and date fields without changing the frozen market-return v2 output
schema. Typed validation independently checks the fixed Stage 10 dimensions,
reapplies every `as_of` cutoff to instrument and observation availability, and
rejects contradictory timing even when a caller recomputes its lineage digest.
Diagnostics retain the complete cutoff policy and indexed input series/lineage
mapping. Alignment preserves explicit missing cells; correlation uses complete
pairs after outer alignment and refuses truncated inputs. Omitted or explicit
`1.0.0` selection remains byte-compatible with the legacy behavior.

Registry `2.34.0` applies the same explicit-selection rule to
`econometrics.regression`, `econometrics.rolling_regression`, and
`econometrics.stationarity`. Their `2.0.0` variants accept only compatible,
non-truncated trailing Stage 10 v2 return series and never open a store. OLS
uses an intercept by explicit request and publishes classical homoskedastic
covariance, Student-t inference, 95% confidence intervals, fit diagnostics,
and the numerical inference backend. Rolling OLS reuses the same kernel over
every fixed contiguous complete window and does not shrink or fill a window.
Stationarity is a constant-only augmented Dickey-Fuller regression with an
explicit fixed lag, MacKinnon 2010 finite-sample critical values, decisions at
1%, 5%, and 10%, and an explicit unavailable p-value. Forward-return, truncated,
rank-deficient, undersized, and incomplete-window inputs fail closed or return
a typed not-established result. Omitted or explicit `1.0.0` selection remains
byte-compatible with legacy behavior. Exact projection restores registry
`2.33.0`; the frozen v1 catalog remains byte-identical.

Registry `2.36.0` adds explicit `2.1.0` selection only for
`econometrics.regression` and `econometrics.rolling_regression`; their
existing `2.0.0` contracts and all omitted-v1 behavior remain unchanged.
The `2.1.0` request requires a 95% confidence level, covariance selection,
`hac_lag` from 0 through 18, and `diagnostic_lag` from 1 through 18. HC1
uses the `n/(n-k)` finite-sample correction; HC3 divides each score by
`1-h_i`; fixed-lag Newey-West uses Bartlett weights and the same
`n/(n-k)` correction. Robust coefficient tests and intervals use the fixed
asymptotic-normal reference and label the standardized statistic `z`, not
`t`.

Residual diagnostics are ordered Ljung-Box, Koenker-Breusch-Pagan, and
Jarque-Bera. Ljung-Box uses centered residuals, a caller-fixed lag,
model degrees-of-freedom zero, and requires a contiguous sample of at least
`max(8, 2*lag+1)`. Koenker-Breusch-Pagan uses `n*R-squared` from a
forced-intercept auxiliary regression on nonconstant original predictors.
Jarque-Bera uses population skewness and excess kurtosis. Each test has a
chi-square asymptotic reference, a fixed 5% decision, and its own explicit
not-established status; an unavailable diagnostic does not silently change
the coefficient fit. HAC inference is not established across a missing-data
gap or when its lag is not below the complete sample. HC3 inference is not
established at unit leverage.

Rolling `2.1.0` reuses the exact standalone kernel in every fixed contiguous
window. A rolling window is at least 8, at least `2*diagnostic_lag+1`, and
strictly greater than `hac_lag`; no incomplete window is shrunk or filled.
The research contract records model version `2.1.0`, covariance, lag,
inference distribution, diagnostic reference, exclusions, and lineage. The v2
catalog is version `2.3.0` with 20 contracts at SHA-256
`7de5126d50c438d0bb35cb82a9a0dd282251de3acceeb850d69ca20f894ef7b9`.
The variants consume only caller-supplied typed values and add no store,
provider, credential, migration, scheduler, export, hosting, or deployment.
Exact projection restores byte-exact registry `2.35.0`; the frozen v1
catalog remains byte-identical.

Registry `2.37.0` adds explicit `2.1.0` selection for
`econometrics.stationarity` while preserving its ADF-only `2.0.0` bytes.
The request declares separate `adf_lag` and `kpss_lag` values from 0 through
18, a constant deterministic term, and one significance level. Level KPSS
demeans the complete contiguous sample, forms residual partial sums, and uses
a caller-fixed Bartlett long-run variance. Decisions use the published 1992
level critical values at 1%, 2.5%, 5%, and 10%; p-values remain explicitly
unavailable. The joint record distinguishes evidence consistent with level
stationarity, evidence consistent with nonstationarity, inconclusive evidence,
and conflicting evidence. Missing values, zero demeaned variance, invalid
bandwidth, or nonpositive long-run variance produce typed not-established
results rather than implicit row deletion or bandwidth selection. Catalog
`2.4.0` has 22 contracts and projects exactly to registry `2.36.0`.

Registry `2.38.0` adds explicit `2.0.0` selection for
`econometrics.structural_breaks`. The request provides 2 through 20 compatible
trailing return series, an intercept flag, significance, limit, and
`break_index`; the index is zero-based and names the first post-break aligned
row. Outer alignment is preserved and every row must be complete. The kernel
fits the exact classical OLS implementation to the pooled, pre-break, and
post-break samples. Each segment must have more rows than fitted parameters and
must be full rank. With `k` fitted parameters and `n` complete rows, the
reported statistic is
`((RSS_pooled - RSS_split) / k) / (RSS_split / (n - 2*k))` and its tail uses
an F distribution with `k` and `n - 2*k` degrees of freedom.
The F reference is exact in finite samples only under Gaussian,
homoskedastic, independent errors and the standard exogenous fixed-design
linear-model conditions. Zero split RSS, materially negative RSS reduction,
missingness, invalid segment degrees of
freedom, and rank deficiency fail closed. The result includes one test summary,
three fit summaries, and a pooled/pre/post coefficient matrix. This is one
caller-declared break only: there is no automatic search, sup-Wald procedure,
multiple-testing adjustment, or multiple-break claim. Catalog `2.5.0` has 24
contracts and projects exactly to registry `2.37.0`.

Registry `2.39.0` adds an explicit `3.0.0` variant of
`econometrics.regression` without changing its v1, `2.0.0`, or `2.1.0`
contracts. The strict request contains two through five compatible Stage 10
horizon-one trailing return series, one `analysis` enum, constant
deterministic specification, fixed `lag_order` from zero through four, a
declared significance, explicit source/target index sentinels, and a limit no
greater than 5,000. The three mutually exclusive analyses are:

- `engle_granger_cointegration`: exactly two ordered series. Compatible log
  returns are cumulatively summed and compatible simple returns use
  `ln(1+r)`; nonpositive factors fail closed. The first stage is
  `level_0 = intercept + slope * level_1 + residual`. Its residual ADF has
  no deterministic term and a caller-fixed augmentation lag. The decision uses
  MacKinnon 2010 `tau_c`, N=2, finite-sample polynomial critical values at
  1%, 5%, and 10%. No p-value is reported, and I(1) integration order remains
  an explicit unverified assumption.
- `vector_autoregression`: two through five ordered return series and a
  caller-fixed lag from one through four. Each equation has a constant and all
  lagged system values. Results expose equation OLS summaries, intercepts,
  coefficient names, one target-by-source matrix per lag, and the
  degrees-of-freedom-adjusted residual covariance. No automatic lag selection,
  impulse response, stability claim, or forecasting claim is made.
- `granger_causality`: the same fixed VAR plus distinct source and target
  indices. It compares the unrestricted target equation with a restricted
  equation that removes every lag of the selected source. The reported exact
  finite-sample F tail requires the classical Gaussian, homoskedastic,
  independent-error, exogenous fixed-design conditions. The result describes
  conditional predictive precedence, never structural causality.

Common incomplete leading and trailing rows may be trimmed and reported;
interior incomplete rows are retained and force a typed not-established result.
Rows are aligned observations, not asserted calendar adjacency. Catalog
`2.6.0` has 26 contracts and projects exactly to registry `2.38.0`.
The registry keeps the 57 logical names, 9 versioned policies, and 13 variants.

These additions consume only caller-supplied typed values and add no store,
provider, credential, migration, scheduler, export, hosting, or deployment.
Their frozen independent vectors, hostile-boundary checks, direct/HTTP parity,
and legacy-version isolation are executable acceptance evidence. The frozen v1
catalog remains byte-identical.

Registry `2.46.0` completes the first canonical-access tool increment. It keeps
the recovered 57-name compatibility inventory intact, exposes 61 active
logical names, 12 version-selection policies, and 16 explicit variants. The
three existing macro catalog/series names gain explicit `2.0.0` variants;
`market.get_volume_series` and `macro.get_release_calendar` are additive native
names. The frozen v1 catalog remains byte-identical. The v2 catalog is version
`2.9.0` with 40 contracts at SHA-256
`e6fa88fa63856247ab00073a14ff1321cfafa05d5323a22c26008e81d0b1eb5c`.
Exact projection removes only this increment and restores registry `2.45.0`.

Registry `2.47.0` completes the next three store-free analytical increments.
It exposes 65 active logical names, 14 version-selection policies, and 18
explicit variants. The v2 catalog is version `2.10.0` with 52 contracts at
SHA-256 `381a78aa59682fbf36cc90acc146d2cfa5b43ea90537b7356eca701df0794fbf`.
Exact projection removes only this increment and restores byte-exact registry
`2.46.0` and catalog `2.9.0`; the generated v1 catalog remains byte-identical.

Registry `2.48.0` adds the store-free
`market.technical_indicators@2.0.0` successor. It exposes 65 logical names,
15 version-selection policies, and 19 explicit variants. The v2 catalog is
version `2.11.0` with 54 contracts at SHA-256
`864a4d07afbf2558a331d30275cf21f4e31521142ee2d9d010dc26cc5680a757`.
Exact projection removes only this policy and restores byte-exact registry
`2.47.0` and catalog `2.10.0`; the generated v1 catalog remains
byte-identical.

The v2 indicator operation accepts one through five unmodified scalar series
from `market.get_price_series` and `market.get_volume_series`. Every call
contains one explicit indicator and only its relevant parameters. Close is
always required; high/low and provider-native volume are required where the
formula needs them. Inputs must share one instrument, period grid, capture
identity, selection mode, cutoff, and retained-availability contract.
Truncated or raw-missing input fails closed. Outputs preserve the bar grid,
carry conservative source-derived availability and path-free lineage, and
represent warm-up or undefined values with an explicit missing reason.
Rolling dispersion uses sample standard deviation; EMA and MACD use SMA
seeds; ATR, RSI, and ADX use Wilder smoothing. The outputs are descriptive
transformations and establish no trading signal, portfolio, or position
semantics.

`data.quality_audit@2.0.0` accepts one through twenty supplied typed Stage 10
return series. It validates lineage, reports requested and observed coverage,
explicit missing values, absent calendar dates, duplicates, out-of-order rows,
availability/cutoff metadata, and source truncation. Calendar discontinuities
are observations only: the tool does not claim an exchange-session calendar or
repair the input. A truncated input remains auditable but cannot establish
complete coverage.

`timeseries.transform@2.0.0` accepts one compatible, non-truncated trailing
return series and one explicit operation. It supports fixed-window rolling
mean, sample standard deviation, minimum, or maximum; full-sample-centered ACF;
Durbin-Levinson PACF; fixed-lag Ljung-Box with zero fitted-model degrees of
freedom; and simple- or log-return drawdown episodes inferred from the typed
return definition. Operation-specific parameters are required and extraneous
parameters are rejected. Terminal missingness may be trimmed and reported;
an interior gap is never bridged or silently filled.

The four native `stats.*` tools are version `1.0.0`, store-free, and accept
only compatible, non-truncated Stage 10 trailing-return series.
`stats.distribution_diagnostics` uses type-7 sample quantiles and declared
population central moments. `stats.covariance_matrix` outer-aligns two through
twenty inputs and then uses one joint-complete sample with the `n-1` sample
denominator for covariance and correlation.
`stats.bootstrap_confidence_interval` requires an unsigned 32-bit seed, 100
through 5,000 IID resamples, a mean or median statistic, and a declared 90%,
95%, or 99% type-7 percentile interval; the result echoes the seed and
deterministic generator metadata. `stats.principal_components` uses the same
joint-complete sample, an explicit covariance or correlation basis, and a
bounded requested component count. Eigenvalues are descending with
deterministic tie handling, and each eigenvector sign is canonicalized by
making its largest-absolute loading positive. Scores are optional and bounded
by the common workload budget.

All six operations inherit and revalidate point-in-time and return-definition
metadata from their typed inputs. They add no store, provider, credential,
migration, scheduler, export, hosting, or deployment surface.


Retirement requires roadmap approval, contract-fixture updates, a migration
guide, and evidence that the local portal and other registered consumers have
moved. No deprecation process authorizes removal of stored data.


## Fixed local Calendar Inspector

The loopback-only Canonical Data Inspector is not a public composable tool and
does not broaden the tool manifest. Its fixed FMP economic-calendar view
accepts only bounded filters plus `mode=current|history`. Current is the
default: it validates and parses the singleton latest-response cache and, before
that cache exists, falls back to the newest validated legacy row per
conservative event identity. History combines consecutive material legacy
changes with append-only migration-0018 event versions. Both modes retain the
existing bounded pagination and columns, expose no response body or writable
connection, and use the fixed macro-store read path.

The same loopback-only Inspector has an additive `options-surfaces` view over
the existing market option relations. It accepts only the fixed 15-ETF
universe, target DTEs `1`, `2`, `3`, `7`, `14`, `30`, `60`, `90`, `180`, and
`365`, exact expiration, `call|put`, `present|missing|excluded`, direction,
and bounded pagination. It chooses the latest paper/indicative capture for
each underlying and selected-expiration cohort, supports the legacy singleton
`target_dte` scope and the broad sorted `target_dtes` scope, and exposes
contract, quote/Greek, open-interest, close-price, underlying-quote, and
explicit synchronized-input missingness fields. Open interest, close price,
underlying quote, rate curve, dividend set, and expiry-model inputs each expose
their persisted state and missing reason rather than making nulls ambiguous.
It exposes no SQL, caller
path, provider request, credential, writable connection, or ingestion action.
The legacy `spy-options` view remains unchanged.

## Security requirements

- The service SHOULD bind to loopback by default.
- Browser and API clients MUST have no SQL console or ingestion endpoint.
- The manifest MUST reveal schemas and limits, not local paths or credentials.
- Error responses MUST be `no-store` and sanitized.
- The host MUST retain the recovered defensive headers or stricter equivalents.
- The optional live intraday capability MUST be separately authorized and
  rate/timeout bounded; it MUST never log its credential or credential-bearing
  URL.

## Acceptance evidence

The contract is Accepted; each implementation milestone remains unimplemented
until its applicable evidence is recorded.

### Registry and compatibility

- The canonical manifest test proves exactly 57 reserved compatibility names,
  65 current active logical names, and the current active family counts above.
  The historical Stage 1 projection separately proves exactly its two declared
  milestone names and reports a non-active milestone status.
- Every entry has generated input/output schemas, examples, limits, versions,
  store ownership, and one resolvable operation graph.
- Recovered request/response fixtures pass, or each conflict is recorded and
  explicitly resolved.
- HTTP and in-process dispatch return semantically equivalent results.

### Validation and routing

- Every accepted example validates; unknown names, extra top-level fields,
  duplicate JSON keys, wrong types, unsupported enums, and excessive bodies
  fail before domain execution.
- Synthetic output violations fail closed.
- Tests monkeypatch or instrument writer entry points and prove that no tool
  initializes, migrates, or writes a store.
- Temporary tests cover four distinct split paths, duplicate-physical-path
  rejection, and unavailable and wrong-domain files.
- Caller-supplied path, SQL, table, PRAGMA, import, and credential attempts are
  rejected.

### Data and analytical semantics

- Golden fixtures cover latest, `as_of`, and first-release selection,
  tombstones, restoration, revisions, date-only availability, and explicit
  unsafe outcomes.
- Composed typed operations preserve units, time, missingness, lineage,
  research contracts, and return-target contracts.
- Statistical golden vectors and independent reference calculations cover
  estimators, covariance choices, multiplicity methods, ties, finite-number
  handling, and insufficient samples.
- Range-invariance tests prove that adding future observations does not change
  an earlier causal result.

### Determinism and bounds

- Repeated identical calls over frozen temporary stores produce identical
  canonical JSON and analysis IDs.
- Every hard limit has boundary, just-below, and just-above tests.
- Cancellation, deadline, result-size, concurrency, and output-validation
  failures have bounded structured responses.
- No response or receipt contains secrets, local paths, SQL, raw artifacts, or
  non-finite JSON values.

### Consumer acceptance

- The Agent Tools UI can generate forms from the manifest, render every result
  shape, reuse composable series, and show bounded raw strict JSON.
- Wide and nested results remain inspectable without silently dropping fields.
- Deprecation and version-selection fixtures are visible to registered
  consumers.

## Change control

Changes to public names, schemas, routing, availability, return contracts,
statistical definitions, resource ceilings, or live capabilities require
integration-owner review. Changes to a primitive require impact analysis of
every registry entry bound to it. The registry digest and acceptance evidence
MUST be updated in the same reviewed change.
