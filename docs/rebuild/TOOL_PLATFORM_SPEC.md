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

- the accepted 57-name compatibility surface;
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
document are reserved under eventual API version `1.0`, subject to recovered
contract fixtures and the versioning rules below. Stage 1 validates only
`macro.get_series` and `timeseries.describe`; that registry is explicitly a
milestone subset and MUST NOT claim full 57-tool restoration or reuse any
reserved name with different semantics.

The accepted public names are:

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

The manifest endpoint and call endpoint are `GET /api/agent-tools`
and `POST /api/agent-tools/call`. Route restoration remains subject to route
fixtures; this document does not claim that either endpoint currently exists.

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

Retirement requires roadmap approval, contract-fixture updates, a migration
guide, and evidence that the local portal and other registered consumers have
moved. No deprecation process authorizes removal of stored data.

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

- The final canonical manifest test proves exactly 57 unique names and the
  family counts above. The Stage 1 manifest separately proves exactly its two
  declared milestone names and reports a non-active milestone status.
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
