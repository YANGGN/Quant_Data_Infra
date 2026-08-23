# System Registry Specification

## Status

**Accepted.** The canonical registry path is
`config/system_registry.json`; the optional host override remains
`QUANT_SYSTEM_REGISTRY_PATH`. The current accepted configuration is revision
`2.30.0`, schema `1.8.0`. The former `2.22.0`/`validated` working candidate is
rejected under [ADR 0011](../adr/0011-retire-proposed-bls-cpi-release-archive.md)
and is not an accepted registry revision. Registry validation and artifact
presence are declarative, not authorization or evidence of provider execution,
canonical publication, public exposure, or scheduler operation. Because the
candidate never entered the active configuration lineage, it remains absent
from the later additive `2.23.0` through `2.30.0` revisions. For any
operational task, first read the
[current operating envelope](CURRENT_OPERATING_ENVELOPE.md).

The documented lineage preserves the Stage 8 set of 57 tools, four
local-private dashboard exposures, eight disabled
`manual_fixture_only` jobs, and one bounded `manual_only`/fixture-only JSON
Atlas export. It also declares the isolated Stage 9, private Stage 10
market-history, private Stage 11 BEA/EIA candidate, and bounded
fixture-validated FMP stock-latest news resources. None of those private
datasets has a tool, dashboard, or Atlas exposure. Registry `2.13.0` also
declares only the offline fixture collector
`market.stage12b.fmp_daily_incremental_fixture` with handler
`market.stage12b_fmp_daily_incremental_fixture`, as defined by the
[Stage 12B contract](STAGE12B_INCREMENTAL_MARKET_V1.md). It selects
`data/market.sqlite` as the current market default; neither declaration opens
or accepts an existing file, and the Stage 12B declaration has no live provider,
API-key, network, default/retained-store, migration, public-consumer, or
scheduler authority.
Registry `2.14.0` adds only
`market.stage12c.fmp_daily_incremental_manual`, defined by the
[Stage 12C contract](STAGE12C_MARKET_GAP_V1.md). Stage 12C is complete and
independently verified; the binding provider outcomes and final target state
are in its [immutable evidence record](STAGE12C_EVIDENCE.md), not inferred from
the declaration. Revision `2.14.0` adds no migration, public exposure, or
scheduler declaration. Its exact historical projection restores `2.13.0`
before Stage 12B and earlier evidence. Stage 12D is complete and independently
verified under its
[no-transfer adoption/freeze contract](STAGE12D_PROJECT_LOCAL_OPERATIONALIZATION.md)
and [evidence record](STAGE12D_EVIDENCE.md); it did not change this registry.
Registry `2.15.0` changes only the remaining current store defaults to
`data/macro.sqlite`, `data/company.sqlite`, and `data/news.sqlite`, matching
the files already present beside `data/market.sqlite`. It creates, copies,
moves, opens, or mutates no SQLite file. Its exact historical projection
restores registry `2.14.0` and the three prior `_data` declarations before
Stage 12C/D or earlier evidence is reproduced.
Registry `2.16.0` adds macro ordinal 13, two private datasets, and the fixed
official BEA/BLS GDP/CPI vintage collectors. Registry `2.17.0` adds macro
ordinal 14 and only the private Philadelphia Fed historical and BLS current
employment collectors. Registry `2.18.0` adds macro ordinal 15 and two
manual-only deep-history collectors; both successor revisions reuse the two
private official-vintage datasets. Registry `2.19.0` adds only the manual-only
`fmp.macro.gdp_cpi_release_calendar_history` collector, reusing
`fixture.macro.economic_calendar` and the existing `macro.official_vintages`
tool dependency. It adds no migration, dataset, job, scheduler, new public-
consumer identity, or surprise table. The existing `macro.release_surprises`
tool is now established over the calendar and official-vintages relations. The
`2.19.0` declaration adds no new tool, dashboard, export, credential, or
caller-selected path. On demand, GDP selects one best-available record per
quarter: an exact-date BEA first release (`advance`, or source-native
`initial`), otherwise an exact-date `second`, then an exact-date `third`.
Every selection compares FMP consensus with the BEA actual from the same
release date and stage; FMP GDP actual is never used. Equivalent reviewed
aliases collapse only when their same-date values agree; conflicts fail
closed. Later stages are explicitly marked `is_fallback`. The current
canonical read returns all 55 quarters from `2012Q4` through `2026Q2`: 10
advance, one initial, 10 second, and 34 third; 54 have numeric surprises and
`2012Q4` is `missing_consensus`. At that revision, the 864 CPI results use
actual and consensus from the same FMP event version. This derived policy adds
no migration, physical table, provider request, or scheduler. At revision
`2.19.0`, the inventory was 37 migrations, 50 datasets, and 35 collectors.
The exact
`2.19.0` to `2.18.0` projection removes only the FMP calendar collector; the
`2.18.0` to `2.17.0` projection removes only
migration 0015 and the two history collectors; the `2.17.0` to `2.16.0`
projection removes only
migration 0014 and the two employment collectors; the existing `2.16.0` to
`2.15.0` projection then removes only the GDP/CPI vintage slice.

Registry `2.20.0` adds only
`fmp.macro.employment_release_calendar_refresh` and its reciprocal binding to
the established economic-calendar dataset. It adds no migration, dataset, job,
timer, surprise table, or public route. A separate explicit user decision
authorized the fixed refresh unit recorded in the
[current operating envelope](CURRENT_OPERATING_ENVELOPE.md); that host-level
scheduler state is not a registry declaration. Its exact projection restores
byte-exact `2.19.0`.

Registry `2.21.0` adds macro migration
`macro:0016_fmp_calendar_wholesale_evidence`, the private
`macro.fmp.economic_calendar_evidence` dataset, and the manual-only
`fmp.macro.us_economic_calendar_wholesale` collector. The dataset owns only
`fmp_economic_calendar_captures` and `fmp_economic_calendar_rows`; it adds no
public tool, dashboard, export, job, timer, or surprise table. The canonical
inventory is now 38 migrations, 51 datasets, and 37 collectors. Its exact
`2.21.0` to `2.20.0` projection removes only those three declarations and
restores byte-exact `2.20.0`.

ADR 0011 rejects the never-active `2.22.0` candidate
`macro:0017_bls_cpi_release_archive`, its archive datasets, collector, and
relations. It is not part of the 38/51/37 inventory, has no migration
allocation or accepted completion evidence, and must not be activated. CPI
surprises use FMP actual and consensus under the documented complementary-row
repair rule; the retired archive cannot validate, fill, replace, or otherwise
affect them. The completed `2.16.0` BLS annual-revision snapshots and current
BLS refresh remain separate accepted declarations.

Registry `2.23.0` adds only the manual-only
`fmp.macro.treasury_yield_curve_history` collector and reciprocal bindings to
the existing generic macro evidence/catalog datasets and Treasury-curve
dataset. Registry `2.24.0` then adds only the credential-free, manual-only
`nyfed.macro.overnight_rates_history` collector and reciprocal bindings to the
three existing generic macro datasets. It stores only the EFFR, OBFR, TGCR,
BGCR, and SOFR headline percentages in existing macro relations;
distributions, volumes, averages/index values, and facilities remain outside
revision `2.24.0`.

Registry `2.25.0` adds only the credential-free, manual-only
`nyfed.macro.repo_facility_usage_history` collector and reciprocal bindings
to the same three generic macro datasets. It stores daily ON RRP and SRF
accepted USD amounts in the existing macro evidence, release, snapshot,
version, and current-observation relations. Explicitly identified small-value
and operational-readiness exercises are excluded; same-day operations for one
facility are aggregated. It adds no migration, dataset, job, timer, public
tool, dashboard, export, credential, or caller-selected path. At revision
`2.25.0`, the inventory was 38 migrations, 51 datasets, and 40 collectors.
Exact projection
removes the repo-facility declaration and bindings to restore byte-exact
`2.24.0`, then removes the headline-rate declaration and bindings to restore
byte-exact `2.23.0`, before the Treasury projection restores the established
pre-Treasury registry.

Registry `2.26.0` adds only the credential-free, manual-only
`nyfed.macro.soma_summary_history` collector and reciprocal bindings to the
existing SOMA evidence and summary datasets. One bounded aggregate-summary
response is normalized into nine declared weekly components in the existing
`soma_source_artifacts`, `soma_snapshots`, `soma_snapshot_artifacts`, and
`soma_summary_components` relations. Source-empty values remain explicit
missingness; security-level identity is rejected. It adds no migration,
dataset, job, timer, public tool, dashboard, export, credential, or
caller-selected path. The current inventory is 38 migrations, 51 datasets,
and 41 collectors. Exact projection removes only the SOMA collector and its
two reciprocal bindings to restore byte-exact `2.25.0`, after which the
existing projection chain applies unchanged.

Registry `2.27.0` adds only three credential-free, manual-only collectors
and reciprocal bindings to the three existing generic macro datasets:
`federal_reserve.macro.h41_history` for three H.4.1 series delivered in one
FRED ZIP, `chicagofed.macro.nfci_history` for NFCI and ANFCI in one CSV, and
`bis.macro.credit_conditions_history` for three U.S. private-sector credit
series in two CSV responses. It adds no migration, dataset, job, timer, public
tool, dashboard, export, credential, or caller-selected path. That revision's
inventory is 38 migrations, 51 datasets, and 44 collectors, and its registry
source SHA-256 is
`9be40d07a7984b223303174a079b19a2a57fa7fcb5a56678482533dc53e4f8ff`.
Exact projection removes only those three collectors and their reciprocal
bindings to restore byte-exact `2.26.0`.

Registry `2.28.0` adds only the credential-free, manual-only
`nyfed.macro.cmdi_history` collector and reciprocal bindings to the same
three existing generic macro datasets. One fixed NY Fed workbook supplies the
overall-market, investment-grade, and high-yield Corporate Bond Market
Distress Index series. It adds no migration, dataset, job, timer, public tool,
dashboard, export, credential, or caller-selected path. The current inventory
at that revision was 38 migrations, 51 datasets, and 45 collectors, and its
registry source SHA-256 was
`131b4f24b2e8d7d9dfa7fedb5de37cf16455aa5a0ee91d0aae69315fd0ac2aef`.
Exact projection removes only the CMDI collector and its reciprocal bindings
to restore byte-exact `2.27.0`.

Registry `2.29.0` adds only three manual-only collectors and reciprocal
bindings to the same three existing generic macro datasets. Treasury Fiscal
Data supplies the daily Treasury General Account closing balance in one fixed,
credential-free request. EIA supplies the weekly Lower-48 working natural-gas
storage series in one fixed request through the existing `EIA_API_KEY`
resolver. NBER supplies one fixed business-cycle chronology response, which is
converted at capture time into a monthly 0/1 U.S. recession indicator. It adds
no migration, dataset, job, timer, public tool, registry dashboard, export,
credential mechanism, or caller-selected path. The inventory at that revision was 38
migrations, 51 datasets, and 48 collectors, and its registry source SHA-256 was
`d7a5ba0a556abc9faa6d726e162969d4c11f0a5da614865eff23318d16ca55ff`.
Exact projection removes only these three collectors and their reciprocal
bindings to restore byte-exact `2.28.0`; the earlier projection chain remains
unchanged.

Registry `2.30.0` adds only the credential-free, manual-only
`bls.macro.price_wage_productivity_history` collector and reciprocal
bindings to the same three existing generic macro datasets. One BLS API POST
requests `WPSFD4` Producer Price Index - Final Demand, seasonally adjusted;
`CES0500000003` Average Hourly Earnings - Total Private, seasonally adjusted;
and `PRS85006092` Nonfarm Business Labor Productivity, percent change from
the previous quarter. It adds no migration, dataset, job, timer, public tool,
registry dashboard, export, credential, credential mechanism, or
caller-selected path. The current inventory is 38 migrations, 51 datasets,
and 49 collectors, and the current registry source SHA-256 is
`4945c54e695b093112e6e7425dc214e5288396cf309d23ccf1aba33e386ee1e6`.
Exact projection removes only this collector and its reciprocal bindings to
restore byte-exact `2.29.0`; the earlier projection chain remains unchanged.

The frozen Stage 10 projection remains `2.8.0`/`1.6.0`; the frozen Stage 9
projection remains `2.7.0`/`1.5.0`; the frozen Stage 7 projection remains
`2.5.0`/`1.3.0` with zero exports; the frozen Stage 6 projection remains
`2.4.0`/`1.2.0` with zero jobs; and the frozen Stage 5 projection remains
`2.3.0`/`1.1.0`. The Stage 8 declaration is a deliberate forward
reconstruction, not recovered 13-dataset Atlas parity.
Registry declaration and lifecycle metadata do not replace Stage 8 evidence:
its primary fixture gate and independent verification passed, and its bounded
exit gate was accepted on 2026-08-14 with an explicit waiver for unavailable
in-app browser automation. The waiver is not browser-pass evidence. The Stage
9 primary and independent offline gates and bounded live receipt passed. The
retained Stage 10 base and extension receipts are complete and independently
re-inspected. The bounded Stage 11 population and its private completion
receipt are complete. Registry declaration alone is not evidence of any live
result; the retained point-in-time receipts remain authoritative.

## Purpose

One versioned, machine-readable registry will be the declarative source of truth for:

- operational stores and their path overrides;
- ordered migration resources;
- dataset identity, store ownership, architectural layer, temporal policy, and freshness;
- collectors, semantic no-write gates, inputs, outputs, and scheduling jobs;
- physical write-lock and backup needs;
- read-only tool and dashboard exposure;
- derived materializations; and
- Atlas snapshot exports.

The registry prevents path, ownership, workload, and exposure policy from being copied independently into migrations, collectors, the scheduler, tool code, dashboard navigation, and the Atlas exporter.

## Boundaries and non-goals

The registry is declarative configuration, not:

- a database migration ledger;
- a replacement for each store's applied dataset registry or ingestion runs;
- a secret store or environment-file loader;
- a scheduler execution log;
- executable collector or SQL source;
- a place for raw provider payloads;
- an authorization mechanism for arbitrary SQL, tables, files, or database paths; or
- evidence that a declared component has been implemented or validated.

The registry names environment variables but never stores their values. Consumers receive an explicit environment mapping from the process. Registry loading does not search for or read a .env file.

## Canonical representation

The first implementation should use UTF-8 JSON because Python 3.11 can parse and validate it without a third-party dependency. The registry has one canonical file and one declared schema version. YAML in this document is illustrative only and must not be loaded, copied into production, or treated as a migration manifest.

Canonical serialization requirements:

- a top-level JSON object;
- no duplicate object keys;
- no comments, NaN, Infinity, or implementation-specific values;
- stable string identifiers and explicit arrays where order is meaningful;
- normalized forward-slash resource paths;
- semantic schema and registry versions;
- deterministic canonical serialization for hashing; and
- strict rejection of unsupported schema versions.

Object key order has no meaning. Migration arrays, declared dependency arrays where explicitly marked ordered, and presentation order arrays do have meaning.

## Registry object model

### Top-level document

| Field | Type | Required | Contract |
| --- | --- | --- | --- |
| schema_id | non-empty string | Yes | Identifies this registry schema, independent of a deployment |
| schema_version | semantic version | Yes | Selects validation rules; unsupported major versions fail closed |
| registry_version | semantic version | Yes | Changes whenever declarative content changes |
| status | enum | Yes | proposed, validated, or active |
| stores | array of Store | Yes | Exactly the four operational stores in this architecture |
| migrations | array of Migration | Yes | Ordered through explicit store/order fields, not object key order |
| datasets | array of Dataset | Yes | Dataset ownership and contracts |
| collectors | array of Collector | Yes | Manual or scheduled data-producing units |
| jobs | array of Job | Yes | Orchestration of collectors and exports |
| tools | array of ToolExposure | Yes | Fixed read-only tool contracts |
| dashboard | array of DashboardExposure | Yes | Fixed pages, views, and table/query allowlists |
| exports | array of Export | Yes | Derived analytical and Atlas snapshot outputs |
| presentation_order | object of ID arrays | No | Stable user-facing order only; never changes ownership semantics |

Every top-level collection is present, even when empty during a staged reconstruction. An active registry cannot omit required store, dataset, migration, or consumer coverage.

### Store

| Field | Type | Required | Contract |
| --- | --- | --- | --- |
| id | namespaced identifier | Yes | One of market, macro, company, news |
| default_path | safe relative path | Yes | Relative to an explicit project root; never absolute or traversing |
| path_env | environment-variable name | Yes | QUANT_MARKET_DB_PATH, QUANT_MACRO_DB_PATH, QUANT_COMPANY_DB_PATH, or QUANT_NEWS_DB_PATH |
| control_tables | array of identifiers | Yes | Includes schema_migrations, dataset_registry, and ingestion_runs |
| migration_order | array of migration IDs | Yes | Complete ordered list for this store |
| write_coordination | object | Yes | Declares physical_store lock scope, timeout, and deterministic ordering policy |
| backup | object | Yes | Declares SQLite online-backup behavior and verification requirements |
| legacy_compatibility | object | No | Must be absent from an active runtime registry; future one-shot import requires a separate contract |

Required default paths:

| Store | Default path | Override |
| --- | --- | --- |
| market | data/market.sqlite | QUANT_MARKET_DB_PATH |
| macro | data/macro.sqlite | QUANT_MACRO_DB_PATH |
| company | data/company.sqlite | QUANT_COMPANY_DB_PATH |
| news | data/news.sqlite | QUANT_NEWS_DB_PATH |

The current market default is fixed by ADR 0009. The superseded
`data/market_data.sqlite` name is not a fallback, alias, or second active
market store. An exact historical registry projection may restore that prior
declaration only to reproduce already accepted Stage 1–11 evidence; it cannot
be used as current runtime routing.

Likewise, `data/macro_data.sqlite`, `data/company_data.sqlite`, and
`data/news_data.sqlite` are historical registry declarations, not current
fallbacks or aliases.

The legacy `data/quant_data.sqlite` and `QUANT_DB_PATH` pair must not appear in
an active runtime registry. Unified runtime compatibility is retired; a future
one-shot import contract would be separate from Store declarations.

Store path resolution follows this precedence:

1. an explicit path supplied to a narrowly scoped command or test harness;
2. the supplied environment mapping at the store's path_env key; and
3. default_path relative to the supplied project root.

Every resolved path becomes absolute and normalized before opening a connection, deriving a lock, or grouping backups. Empty overrides fail rather than falling back silently.

### Migration

| Field | Type | Required | Contract |
| --- | --- | --- | --- |
| id | stable identifier | Yes | Unique across the registry; normally includes store scope and ordinal |
| store | Store ID | Yes | Store whose ledger receives the migration |
| ordinal | nonnegative integer | Yes | Total order within one store |
| resource | safe relative resource path | Yes | SQL resource; no absolute path or parent traversal |
| sha256 | lowercase hexadecimal digest | For active status | Hash of reviewed immutable bytes |
| semantic_scope | non-empty string | Yes | Human-auditable purpose, not a substitute for SQL |
| dependencies | array of migration IDs | Yes | Must be acyclic and refer to earlier compatible resources |
| reconstruction_state | enum | Yes | unresolved, fixture_validated, or recovered_exact |

Migration resource existence, checksum, declared order, ledger order, and dependency order are validated before a store is initialized or upgraded. A checksum change to an applied migration is an error. Corrections use a new ordinal.

The recovered semantic sequence extends through 0031. The exact SQL and checksums are not inferred from the semantic descriptions. Migration 0031 must preserve the filing/issuer derived-view contract and supporting invariants; it must not invent a physical join table.

### Dataset

| Field | Type | Required | Contract |
| --- | --- | --- | --- |
| id | namespaced identifier | Yes | Stable public identity, independent of a provider name |
| version | semantic version | Yes | Dataset contract version |
| store | Store ID | Yes | Exactly one operational owner |
| layer | enum | Yes | evidence, canonical, or derived |
| physical | object | Yes | Allowlisted table/view/materialization names and relation kind |
| identity | object | Yes | Natural keys, stable identifiers, and version identity |
| temporal | object | Yes | Observation fields, availability fields, precision, timezone rule, inclusive-range behavior, and supported vintage modes |
| revision_policy | enum/object | Yes | immutable_capture, append_version, current_state_capture, or derived_rebuild |
| freshness | Freshness | Yes | Expected cadence, stale threshold, and how freshness is measured |
| quality_contract | object | Yes | Missingness, units, validation rules, and required warnings |
| collector_ids | array of Collector IDs | Yes | Authorized producers; empty for purely derived/read-only inputs |
| tool_ids | array of Tool IDs | Yes | Tools allowed to consume or expose this dataset |
| dashboard_ids | array of Dashboard IDs | Yes | Dashboard exposures that may query it |
| export_ids | array of Export IDs | Yes | Declared analytical/Atlas outputs |
| active | boolean | Yes | Inactive datasets remain resolvable for migrations/history but are not collected |

The physical block may list several relations only when they all serve the dataset's declared layer. Relations serving different layers require separate dataset IDs and explicit lineage. Every named table or view belongs to exactly one dataset contract unless the registry explicitly marks a shared control-plane object.

The temporal block records, at minimum:

- observation/reference-period field and precision;
- available_at or local capture field and precision;
- source-native timezone/no-conversion rule;
- supported latest, as_of, and first_release modes;
- whether historical semantics are true source vintages or local-capture-only;
- inclusive start/end filtering; and
- explicit null/missing-reason behavior.

### Freshness

| Field | Type | Required | Contract |
| --- | --- | --- | --- |
| cadence | enum/object | Yes | intraday, daily, weekly, monthly, event_driven, manual, or an explicit calendar |
| expected_lag | duration | Yes | Expected source-to-local delay |
| stale_after | duration | Yes | Threshold after which health is stale |
| measured_from | enum | Yes | source_period, source_published_at, available_at, or successful_capture |
| calendar | identifier | No | Trading/release calendar when ordinary elapsed time is misleading |
| if_new | boolean | Yes | Whether semantic identity can produce a zero-write outcome |
| health_severity | enum | Yes | informational, warning, or critical |

Freshness never assumes that missing source coverage is a parser failure. Health distinguishes not due, unchanged, delayed source, partial ingestion, failed ingestion, and stale local coverage.

### Collector

| Field | Type | Required | Contract |
| --- | --- | --- | --- |
| id | namespaced identifier | Yes | Stable collector identity |
| version | semantic version | Yes | Behavior contract version |
| handler | implementation identifier | Yes | Resolved from trusted application code; not a shell fragment |
| input_datasets | array of Dataset IDs | Yes | Read dependencies, if any |
| output_datasets | non-empty array of Dataset IDs | Yes | Authorized write targets |
| semantic_identity | object | Yes | Scope-bearing comparison rule and fields intentionally excluded |
| mutation_policy | object | Yes | Append/upsert/version behavior and unchanged semantics |
| workload_bounds | object | Yes | Request, symbol, page, byte, row, and time bounds as applicable |
| retry_policy | object | Yes | Transient classes, attempt bounds, backoff, and Retry-After behavior |
| configuration_env | array of names | Yes | Names only; never values |
| physical_locks | derived marker | Yes | Must be derived from output store paths |
| schedule_eligibility | object | Yes | Manual-only or calendars on which jobs may invoke it |

For an if-new collector, semantic identity includes request scope and material parsed content. Fields known to vary without semantic change may be excluded only by an explicit, fixture-tested rule. A timeout, partial response, schema error, authentication failure, or rate limit cannot compare equal to a successful response.

Fetch happens before physical locks are acquired. The lock set is the set of canonical resolved output-store paths, sorted deterministically. Store-path validation rejects two logical stores that resolve to one physical file before collection can begin.

### Job

| Field | Type | Required | Contract |
| --- | --- | --- | --- |
| id | namespaced identifier | Yes | Scheduler/manual command identity |
| version | semantic version | Yes | Orchestration contract version |
| steps | ordered array | Yes | Collector or export IDs plus continue/fail policy |
| required_stores | derived marker | Yes | Derived from step dataset references, never copied as an independent list |
| calendar | object | Yes | Manual or explicit cadence/calendar gating |
| overlap_policy | enum | Yes | ignore_new or fail_new |
| aggregate_status | object | Yes | Defines success, incomplete, and nonzero behavior |
| receipt | object | Yes | External receipt/log behavior, including zero-write polls |

The registry describes jobs; a platform-specific scheduler definition is generated or validated against them. Scheduler XML or command lines are not embedded as authoritative business logic.

Schema `1.3.0` freezes eight ordered Stage 7 job declarations:
`news-hourly`, `sec-daily`, `options-close`, `macro-daily`,
`market-close`, `expectations`, `company-weekly`, and `macro-monthly`.
Every entry is `manual_fixture_only`, `scheduling_enabled: false`, uses
fixture-only network mode, derives its complete write-store set from steps,
declares bounded retry/timeout and dependency policy, publishes private
versioned receipts, and uses the registered aggregate exit precedence. Exact
external task definitions, timezone, identity, and calendar installation
remain unresolved; no scheduler definition is generated or installed.

Schema `1.5.0` adds exactly one live-enabled but manual-only collector:
`fmp.market.daily_price_backfill`. Its registered output datasets are
`market.fmp.daily_price_evidence`, `market.fmp.instruments`, and
`market.fmp.daily_prices`; they are isolated under
`market:0009_fmp_daily_price_backfill`. It has one request, 22-row,
262144-byte, and 30-second bounds, a no-retry policy, and a semantic
identity that excludes the credential and headers. `FMP_API_KEY` is an
environment-variable name only. There is no job, tool, dashboard, or Atlas
exposure for this collector or its datasets, and its declaration is not
evidence that a provider response was fetched.

Schema `1.6.0`, registry `2.8.0`, adds the bounded Stage 10 market-history
profile: `market:0010_stage10_market_history`, four private datasets, and the
`fmp.market.stage10_universe_capture` and
`fmp.market.stage10_daily_history` manual-only collectors. They use
`FMP_API_KEY` only as a configuration environment-variable name, use no
automatic retry, and have no public consumer. The retained base and extension
population results are recorded in [Stage 10 evidence](STAGE10_EVIDENCE.md).

Schema `1.7.0`, registry `2.9.0`, adds
`macro:0012_stage11_bea_eia_live_history`, six private evidence/canonical
datasets, and three manual-only collectors:
`bea.macro.stage11_nipa_history`,
`eia.macro.stage11_electricity_retail_history`, and
`eia.macro.stage11_petroleum_weekly_stock_history`. `BEA_API_KEY` and
`EIA_API_KEY` are environment-variable names only. The registry declaration
does not imply completion; the completed private candidate state is recorded in
[Stage 11 evidence](STAGE11_EVIDENCE.md).

Registry `2.10.0` keeps schema `1.7.0` and changes no migration, dataset,
collector identity, or public exposure. It canonically records the reviewed
Stage 11 transient retry policy: at most three physical attempts for
connection/timeouts, HTTP 429, or HTTP 500-599, with deterministic one-second
then two-second backoff and no jitter. Historical Stage 11 execution projects
back to the exact `2.9.0` declaration and source digest bound into the
completed receipt. This successor revision neither rewrites that receipt nor
authorizes a new live cohort.

Schema `1.8.0`, registry `2.11.0`, adds the bounded FMP stock-latest news
fixture contract: `news:0005_fmp_stock_latest`, two private news datasets, and
one manual-only collector. Its fixture evidence does not prove complete or live
news coverage and does not authorize another provider request.

Registry `2.12.0` keeps schema `1.8.0` and changes no migration, dataset,
collector, job, tool, dashboard, or export identity. It changes only the current
market default to `data/market.sqlite` under ADR 0009. The
`stage12_registry_profile` projection restores `data/market_data.sqlite` and
registry `2.11.0` before the existing Stage 11/10/earlier projections run.
That compatibility exists only for immutable historical evidence; the old path
is not current routing, a fallback, or an alias.

Registry `2.13.0` keeps schema `1.8.0` and adds the bounded Stage 12B fixture
collector only: `market.stage12b.fmp_daily_incremental_fixture`, handler
`market.stage12b_fmp_daily_incremental_fixture`, `manual_only` schedule
eligibility, one attempt, no transient retry classes or backoff, and finite
fixture bounds of 65,536 bytes, one request, five rows, and 30 seconds. It uses
the existing `0010` capture/version/current model only in explicit temporary
fixture stores and creates no migration, public exposure, or provider authority.
The exact `2.13.0` to `2.12.0` historical projection removes the Stage 12B
declaration and restores the accepted `2.12.0` registry before Stage 12A and
earlier evidence is reproduced. It is not a live, operational, or scheduler
authorization.

Registry `2.14.0` keeps schema `1.8.0` and adds only
`market.stage12c.fmp_daily_incremental_manual`. It creates no migration,
public exposure, or scheduler authority. The exact `2.14.0` to `2.13.0`
historical projection removes that declaration before Stage 12B and earlier
evidence is reproduced. The completed Stage 12C provider work remains bound
only to [its private receipt and evidence record](STAGE12C_EVIDENCE.md);
neither the current registry nor a historical projection alone proves a
provider call or database mutation. The completed Stage 12D no-transfer proof used the existing
`2.14.0`/schema `1.8.0` binding without repinning or modifying it; see its
[immutable evidence record](STAGE12D_EVIDENCE.md).

Registry `2.15.0` keeps schema `1.8.0` and changes only the current macro,
company, and news default basenames to `macro.sqlite`, `company.sqlite`, and
`news.sqlite`. The exact `2.15.0` to `2.14.0` projection restores the prior
declarations and exact Stage 12C/D registry source hash. No database file is
created, renamed, copied, opened, or mutated by this declaration change.

Registry `2.16.0` keeps schema `1.8.0` and adds only
`macro:0013_live_gdp_cpi_vintages`, `macro.official_vintages_evidence`,
`macro.official_vintages`, `bea.macro.live_gdp_vintages`, and
`bls.macro.live_cpi_vintages`. The model retains raw official evidence and
source-release identities without reinterpreting the Stage 11 candidate
relations. The exact historical projection restores `2.15.0`.

Registry `2.17.0` keeps schema `1.8.0` and adds only
`macro:0014_live_employment_vintages`,
`philadelphia_fed.macro.live_employment_vintages`, and
`bls.macro.live_employment_current`. It broadens the isolated official-vintage
model only for the two exact BLS employment series, retaining Philadelphia Fed
RTDSM source-vintage identities with local-capture availability and BLS current
facts in the same versioned lineage. The exact historical projection restores
byte-exact registry `2.16.0`.

Registry `2.18.0` keeps schema `1.8.0` and adds only
`macro:0015_live_macro_history_extension`,
`bls.macro.cpi_current_history`, and
`philadelphia_fed.macro.gdp_cpi_vintage_history`. It reuses the two private
official-vintage datasets. Both collectors are manual-only, one-attempt, and
credential-free; no new scheduler or public exposure is declared. Its exact
historical projection restores byte-exact registry `2.17.0`.

Registry `2.19.0` keeps schema `1.8.0` and adds only the manual-only
`fmp.macro.gdp_cpi_release_calendar_history` collector. It reuses
`fixture.macro.economic_calendar` and the existing `macro.official_vintages`
tool dependency, with no migration, dataset, job, scheduler, public consumer,
or physical surprise table. Its exact historical projection restores
byte-exact registry `2.18.0`.

#### 2026-08-13 Stage 11 runtime compatibility clarification

This explicit runtime override applied only to finish the then-started,
isolated non-production Stage 11 candidate. That candidate and the completed
Stage 10 cohort are pinned to canonical registry source SHA
`7e8ec6fc38d5a24962460d9c78a4df7754d3b29ad4b74c0c5e8ae807cd59ed56`.
Accordingly, neither the clarification nor registry `2.10.0` repins the
completed receipt, earlier BEA evidence, or earlier retail evidence, and
neither changes the Stage 9 or Stage 10 no-retry statements. The candidate is
complete and none of its provider requests may be repeated.

For that completed candidate only, each exact logical BEA request, EIA retail page, and
EIA weekly request is serialized. It may make one initial physical attempt
plus at most two automatic retries. Retrying is permitted only for
connection/timeouts, HTTP 429, and HTTP 500-599. Backoff is deterministic and
has no jitter: one second before the first retry and two seconds before the
second; every physical attempt observes at least one second of pacing. There
is no retry for authentication/authorization, non-429 4xx (including 408),
redirects, wrong MIME, `200` provider-error envelopes, byte/resource bounds,
or parser/schema/configuration/publication/database/integrity errors. No
database write lock or transaction may be held during calls or sleeps, and a
failed physical attempt creates neither evidence nor phase advancement. On
success, existing `*_request_count` fields count physical attempts, while
scope `max_requests` remains a count of logical units.

### ToolExposure

| Field | Type | Required | Contract |
| --- | --- | --- | --- |
| id | namespaced identifier | Yes | Stable public tool name |
| version | semantic version | Yes | Request/result contract version |
| handler | implementation identifier | Yes | Trusted read service |
| read_only | literal true | Yes | Any other value is invalid |
| datasets | array of Dataset IDs | Yes | Complete direct data dependencies |
| input_schema | strict JSON Schema object | Yes | Top-level and every nested object reject additional properties |
| output_schema | strict JSON Schema object | Yes | Finite RFC JSON plus audit envelope |
| examples | bounded array | Yes | Valid arguments with no paths or secrets |
| workload_bounds | object | Yes | Schema-visible input and output limits |
| availability_policy | object | Yes | Required cutoff/vintage behavior |

Tool schemas cannot contain arguments for a database path, SQLite URI, arbitrary SQL, unrestricted table name, filesystem root, or connection string. Dataset dependencies determine stores. The validator recursively checks property names, references, examples, and handler metadata.

The active parity target may claim 57 tools only after all 57 recovered names have strict, bounded input and output schemas and HTTP/in-process parity tests. Until then, the registry status remains proposed or validated rather than active.

### DashboardExposure

| Field | Type | Required | Contract |
| --- | --- | --- | --- |
| id | namespaced identifier | Yes | Stable page, panel, or inspector exposure |
| route | local route | Yes | Fixed application route |
| api_routes | array of local API routes | Yes | Fixed JSON endpoints owned by the exposure; methods remain enforced by the service boundary |
| datasets | array of Dataset IDs | Yes | Direct data dependencies |
| tools | array of Tool IDs | Yes | Guided tool forms available on the exposure |
| relations | array of allowlisted relation IDs | Yes | No caller-created table name |
| filters | strict schema | Yes | Bound fields and values |
| sort_fields | finite array | Yes | Server-owned allowlist |
| pagination | object | Yes | Default and hard maximum |
| visibility | enum | Yes | local_private |

Schema `1.2.0` introduced all ten DashboardExposure fields above. The
frozen Stage 6 `2.4.0` projection declares exactly four ordered local-private
exposures: `stage1.overview`, `stage6.gdp_vintages`,
`stage6.table_inspector`, and `stage6.agent_tools`. Canonical Stage 7
registry `2.5.0`/`1.3.0` preserves those declarations unchanged while
adding only the disabled job catalog. Canonical Stage 8 registry
`2.6.0`/`1.4.0` preserves both historical projections unchanged and adds
only the one bounded export declaration below. Canonical Stage 9 registry
`2.7.0`/`1.5.0` preserves all dashboard declarations unchanged; no FMP
daily-price dataset, relation, or collector appears in a dashboard exposure.
API routes remain fixed registry declarations, not caller-supplied URLs.

Dashboard navigation and the table inspector are generated or validated from
these exposures. Internal raw payload objects remain absent unless a reviewed
exposure explicitly permits them.

### Export

| Field | Type | Required | Contract |
| --- | --- | --- | --- |
| id | namespaced identifier | Yes | Stable export profile |
| version | semantic version | Yes | Output contract version |
| owner / description | strings | Yes | Accountable owner and bounded human-readable scope |
| semantic_dataset_id | namespaced identifier | Yes | Meaning identity, independent of one physical revision |
| kind | enum | Yes | analytical_file or atlas_snapshot |
| format | enum | Yes | json, jsonl, or optional parquet |
| lifecycle | object | Yes | Fixture/manual/network/hosting restrictions and declared lifecycle state |
| datasets | non-empty array of Dataset IDs | Yes | All exported datasets and their owners |
| query_contract | object | Yes | Columns, ordering, filters, caps, and point-in-time policy |
| schema_contract | object | Yes | Output schema version and null/number handling |
| chunking | object | Yes | Deterministic boundaries and maximum sizes |
| freshness | object | Yes | Snapshot health derived from dataset freshness |
| source_mode | enum | Yes | read_only_copy or online_backup |
| consistency | object | Yes | Cohort receipts and cross-store limitations |
| staging | object | Yes | Exact parent, generated child, validation, and atomic promotion |
| optional_dependency | string/null | Yes | Parquet adapter name when used; never silently required by the core |
| benchmark | object | Yes | Explicit format/adoption decision; no inferred package requirement |
| consumers | array | Yes | Fixed consumer IDs, modes, and hosting boundary |
| provenance | object | Yes | Required registry/schema/code/source evidence in the public manifest |

JSON and JSONL are supported by the standard-library core. Parquet is optional and must fail with a clear unavailable-capability error when its declared adapter is absent. Export format does not alter canonical storage.

An Atlas export reads only registered exportable datasets from explicit read-only store copies. Its manifest records registry version, dataset contract versions, per-store backup or read-copy receipts, row/chunk counts, schemas, freshness, hashes, generation time, and source revision. The exporter never labels a multi-store cohort as one atomic database timestamp.

#### Bounded Stage 8 declaration — accepted with browser-automation waiver

The current registry declares exactly one export:
`atlas.fixture_snapshot` version `1.0.0`, semantic dataset
`atlas.fixture_snapshot.core_v1`, owned by `quant_data.atlas`. It is an
`atlas_snapshot` in strict JSON, manual-only, fixture-only, with network and
hosting disabled. Its only consumer is `quant_data_atlas` in
`static_read_only` mode; it has no optional dependency. The explicit
benchmark decision is `not_required_json_atlas_snapshot`: Parquet and DuckDB
are both `not_adopted`.

Its exact reciprocal datasets are `fixture.market.daily_prices`,
`fixture.macro.gdp_vintages`, `fixture.company.issuers`, and
`fixture.news.items`. The four deterministic projections are
`market-prices` (5,000 rows), `gdp-vintages` (1,000),
`company-issuers` (1,000), and `news-items` (5,000), within a 12,000-row,
8 MiB, 30-second whole-snapshot limit. Ordered chunks are limited to 250 rows
and 262144 bytes; empty chunks are omitted.

The cutoff is the aware-UTC export-start instant. Availability is selected at
or before the cutoff, vintage mode is `as_of`, and source date-only precision
uses `completed_date`, never an invented timestamp. A complete deterministic
physical-lock cohort produces one SQLite online backup per store; the exporter
then reopens copies only with SQLite query-only access.

`receipt_chain: complete_baseline_plus_validated_deltas` permits a selected
validated partial correction only over a proved succeeded, validated, complete
baseline and a fully linked validated delta chain. Missing/invalid links,
selected running work, or an unreconciled later failure reject the export.
`partial_scope: forbidden` still requires all four registered projections; it
does not make a partial correction receipt complete on its own.

The export records per-store receipts and `cross_store_atomic: false`, stages
in an exact host-selected root, publishes an immutable revision, updates only
a `current` pointer atomically, and keeps receipts private. No public schema
may expose a database/filesystem path, credential, SQL, raw artifact, private
receipt, body, summary, source URL, run ID, artifact ID, or snapshot ID.

This declaration is not a claim of historical recovered parity, a live export,
or a hosting/deployment action. Its `fixture_validated` lifecycle declaration
does not itself close an acceptance gate. The primary and independent evidence,
plus the user's 2026-08-14 browser-automation waiver, close the bounded Stage 8
gate; see [Stage 8 evidence](STAGE8_EVIDENCE.md).

## Global invariants

Validation fails closed unless all of the following hold:

1. Every ID is non-empty, normalized, unique within its type, and stable under canonical serialization.
2. Every store, migration, dataset, collector, job, tool, dashboard, and export reference resolves.
3. Reference graphs are acyclic where cycles are prohibited; migration dependencies and job steps have deterministic order.
4. Exactly four operational stores exist with the required default paths and environment names.
5. Default paths and resource paths are relative, normalized, non-traversing, and contained by their designated roots after resolution.
6. Every dataset has one store owner, one supported layer, a declared physical relation set, temporal policy, quality contract, and freshness contract.
7. A physical table or view has one owning dataset unless explicitly declared as shared control-plane state.
8. Migration IDs and resources are unique within a store; ordinals are total and gap policy is explicit; active migrations have verified checksums.
9. Applied migration resources are never edited; ledger mismatch prevents writes.
10. Collector output references determine write authorization, required stores, physical locks, and backup boundaries.
11. Physical locks are keyed by canonical resolved database path. Logical aliases to one file cannot acquire independent locks.
12. If-new collectors define a scope-bearing, fixture-tested semantic identity and make zero persistent writes when unchanged.
13. Tools and dashboards are read-only, strict, bounded, and free of caller-selected SQL, relation, connection, or database-path arguments.
14. All date, timestamp, availability, vintage, missingness, unit, and transformation semantics are explicit.
15. Cross-store consumers declare one availability cutoff and return contributing store receipts.
16. Derived datasets and exports cannot be an undeclared source for canonical datasets.
17. Every current Atlas dataset declaration and the one registered export reference each other reciprocally; no fifth dataset or undeclared projection is exported.
18. Atlas profiles use explicit read-only/query-only online backups, stage inside an exact host-selected parent, validate the entire cohort, publish immutable revisions, and update only a current pointer atomically.
19. Public Atlas artifacts omit local paths, credentials, SQL, raw artifacts, private receipts, and other registered excluded fields; private attempt receipts never become a public artifact.
20. Unsupported layers, formats, schema versions, status values, and optional capabilities are rejected rather than guessed.
21. Secrets and secret values do not appear anywhere in the registry, examples, generated documentation, logs, or exported manifests.
22. Registry iteration and generated output are deterministic across processes and platforms.
23. Every network-capable collector is explicitly classified. A manual-only
    declaration grants no provider execution or recurrence; either action
    requires a separate explicit user decision and accepted operational
    contract or evidence. The registry never grants credential use, scheduler
    installation or invocation, public exposure, promotion, retirement, or
    permission to repeat completed work.

## Loading and validation

The loader is standard-library-only and accepts:

- an explicit registry path;
- an explicit project root;
- an explicit environment mapping; and
- an optional strictness/status requirement.

Precedence is explicit path, a named registry-path environment override if the application defines one, then the canonical project-relative path. An explicit empty value is an error. The loader never imports application handlers, opens databases, contacts the network, or reads a .env file.

Validation occurs in phases:

1. **Syntax:** UTF-8, valid JSON, no duplicate keys or non-finite values.
2. **Shape:** supported schema version, required keys, exact primitive/container types, and permitted enums.
3. **Identity:** non-empty normalized IDs, uniqueness, semantic versions, and stable names.
4. **References:** every foreign ID, dependency, exposure, and ordered step resolves.
5. **Paths:** safe defaults/resources, environment-name allowlist, normalized absolute resolution, and root containment.
6. **Ownership:** one dataset/store/layer owner, relation uniqueness, and producer authorization.
7. **Temporal and quality:** complete availability, vintage, precision, missingness, unit, and freshness contracts.
8. **Mutation and locks:** collector outputs, no-write semantics, canonical physical lock derivation, and deterministic acquisition order.
9. **Read exposure:** strict schemas, workload bounds, relation allowlists, no path/SQL arguments, and read_only=true.
10. **Export:** exportability, optional capability checks, snapshot-cohort rules, exact staging containment, and atomic-promotion contract.
11. **Runtime reconciliation:** when requested separately, compare the validated registry with migration ledgers, store-local dataset registries, and generated scheduler/dashboard/export artifacts.

Errors include a stable code and a JSON-pointer-like location. Validation reports all independent static errors in deterministic location order; runtime startup refuses write service if any fatal error remains.

## Consumers

| Consumer | Registry inputs | Required behavior |
| --- | --- | --- |
| Path resolver | Stores, defaults, environment names | Return normalized absolute paths; never implicit unified fallback |
| Migration runner | Store migration order, resources, hashes | Reconcile ledger and apply only reviewed forward migrations |
| Collector runner | Dataset ownership, semantic identity, bounds, outputs | Authorize writes and derive physical locks |
| Job/scheduler tooling | Ordered steps, calendars, overlap and status policy | Validate or generate platform wrapper; preserve aggregate failure |
| Health service | Store paths, datasets, freshness | Report not due, unchanged, delayed, partial, failed, and stale distinctly |
| Tool registry/API | Tool schemas, handlers, datasets, bounds | Expose only validated read-only contracts |
| Dashboard | Dashboard exposures, API routes, tools, relation allowlists | Generate or validate navigation/forms, bounded inspection, and fixed local routes |
| Atlas exporter | Export profiles, dataset ownership, store paths | Read copies, stage exactly, validate cohort, atomically promote |
| Documentation generator | Public metadata only | Produce deterministic reference pages without secrets or runtime claims |

## Generation policy

Generated artifacts may include:

- store/dataset ownership maps;
- migration plans;
- typed identifier constants;
- tool discovery documents and guided forms;
- dashboard navigation, API-route, and inspector allowlists;
- scheduler wrapper inputs;
- health/freshness catalogs;
- Atlas dataset and chunk manifests; and
- human-readable registry reference documentation.

Generation is one-way from a validated registry. Generated files carry the registry version and content hash and are never edited as an alternate source of truth. A check mode regenerates in memory and fails when a committed artifact differs.

Executable handler code, migration SQL, provider parsers, and domain validation are not generated from descriptive strings. The registry selects reviewed implementations; it does not synthesize them.

## Lifecycle and compatibility

1. **Propose:** add or change declarations with status proposed. Unknown checksums or unimplemented handlers are allowed only when explicitly marked and cannot run.
2. **Statically validate:** satisfy syntax, shape, ownership, references, paths, schemas, locks, and export rules.
3. **Fixture validate:** prove migrations, semantic identities, temporal behavior, bounds, failure states, and generated-artifact determinism against isolated temporary stores.
4. **Review:** approve contract versions, migration bytes/checksums, exposure, operational cadence, and compatibility behavior.
5. **Activate:** bump registry_version, set eligible objects active, generate/check derived artifacts, and deploy only after runtime reconciliation.
6. **Operate:** record registry version in migrations, ingestion outcomes, tool metadata, scheduler receipts, backups, and Atlas manifests.
7. **Evolve:** prefer additive fields and migrations. Breaking dataset/tool/output changes increment their major contract version.
8. **Deprecate:** mark an object deprecated with replacement and removal milestone; keep references resolvable until consumers migrate.
9. **Retire:** remove only after no active references remain and historical artifacts retain enough version metadata to be interpreted.

A schema-version change governs registry syntax and validation. A registry-version change governs one configuration revision. Dataset, collector, tool, job, and export versions govern their individual public or operational contracts.

## Illustrative YAML — non-executable

The following is deliberately incomplete YAML for discussion. It is **not the canonical format, not a valid migration manifest, and not executable configuration**. Placeholder values must not be guessed into an implementation.

~~~yaml
# NON-EXECUTABLE ILLUSTRATION ONLY
schema_id: quant-data-system-registry
schema_version: 1.0.0
registry_version: 0.1.0-proposed
status: proposed

stores:
  - id: market
    default_path: data/market.sqlite
    path_env: QUANT_MARKET_DB_PATH
    control_tables:
      - schema_migrations
      - dataset_registry
      - ingestion_runs
    migration_order:
      - market:<reviewed-migration-id>
    write_coordination:
      scope: physical_store
      key: derived_from_canonical_resolved_path

migrations:
  - id: market:<reviewed-migration-id>
    store: market
    ordinal: <reviewed-integer>
    resource: <reviewed-relative-sql-resource>
    sha256: <required-before-active>
    reconstruction_state: unresolved

datasets:
  - id: market.prices_daily
    version: 1.0.0
    store: market
    layer: canonical
    physical:
      relation: prices_daily
      kind: table
      version_relation: prices_daily_versions
    identity:
      natural_key: [instrument_id, trade_date, provider, price_variant]
    temporal:
      observation_field: trade_date
      observation_precision: date
      availability_field: fetched_at
      timezone_conversion: none
      range_semantics: inclusive
      vintage_modes: [latest, as_of]
    freshness:
      cadence: daily
      measured_from: successful_capture
      if_new: false
    collector_ids: [market.close]
    tool_ids: [market.get_returns]
    dashboard_ids: [tables.market_prices]
    export_ids: [atlas.market_prices]

collectors:
  - id: market.close
    version: 1.0.0
    handler: quant_data.collectors.market_close
    output_datasets: [market.prices_daily]
    semantic_identity:
      includes: [request_scope, parsed_observations]
    physical_locks: derived_from_output_store_paths
    workload_bounds:
      symbols: <reviewed-limit>

tools:
  - id: market.get_returns
    version: 1.0.0
    read_only: true
    datasets: [market.prices_daily]
    input_schema:
      type: object
      additionalProperties: false
      properties: <complete-reviewed-schema>
    output_schema: <complete-reviewed-strict-schema>
    workload_bounds: <reviewed-bounds>

dashboard:
  - id: tables.market_prices
    route: /table-inspector
    api_routes: [/api/table-inspector]
    datasets: [market.prices_daily]
    relations: [prices_daily]
    filters: <strict-bounded-schema>
    sort_fields: [trade_date, instrument_id]
    visibility: local_private

exports:
  - id: atlas.market_prices
    version: 1.0.0
    kind: atlas_snapshot
    format: json
    datasets: [market.prices_daily]
    source_mode: read_only_copy
    consistency:
      cohort_receipts: required
      claim_atomic_cross_store_as_of: false
    staging:
      exact_generated_child_under_designated_parent: true
      atomic_promotion: true
~~~

## Acceptance criteria

### Static registry

- Python 3.11 standard library can load the canonical JSON without importing the application.
- Duplicate keys, unknown schema versions, empty IDs, duplicate IDs, wrong types, unsupported enums, and non-finite values fail with stable locations.
- All references resolve and iteration/generation order is deterministic.
- The four store IDs, exact default paths, and exact environment override names match this specification.
- Unsafe, absolute, traversing, empty, and root-escaping defaults/resources fail.
- No registry consumer reads .env implicitly.

### Stores, datasets, and migrations

- Every dataset resolves to exactly one store and one of the three layers.
- Every physical relation has one declared dataset owner or an explicit shared-control designation.
- All four stores initialize independently in temporary paths; tests cannot reach defaults or live paths.
- Per-store migration order is total, resources are unique, active checksums match, and applied bytes cannot change.
- Reconstructed migrations through the recovered 0031 semantic boundary have fixture evidence before parity is claimed.
- Store-local migration and dataset state reconcile with the registry.

### Collectors, jobs, and locks

- Collector outputs derive required stores, authorized writes, physical locks, and backup boundaries.
- Resolved physical-path collisions fail configuration validation before a lock or backup is requested.
- Lock acquisition order is deterministic.
- Fetches and retries occur outside write transactions.
- Each if-new semantic identity has changed, unchanged, scope-changed, partial, error, timeout, and rate-limit fixtures.
- Unchanged produces zero ingestion-run, artifact, snapshot, membership, and canonical writes.
- The Stage 9 FMP collector is fixture-tested only for `SPY`, inclusive
  `2026-07-01` through `2026-07-31`; wrong scope, malformed response, missing
  credential, retry, or public-exposure attempts fail closed without a live write.
- Independent job-step failures remain visible and required failures produce an incomplete/nonzero aggregate result.

### Time and data quality

- Date-only and offset-aware values round-trip without fabricated timezone or precision.
- Inclusive range tests cover monthly observations over a calendar-year boundary.
- Latest, as_of, first_release, and unsupported-vintage behavior have explicit fixtures.
- Missingness, units, scale, price variants, identifiers, currencies, and option captures cannot be silently mixed or filled.

### Tools and dashboard

- Every exposed tool is read-only, deterministic, strict at every object schema, and workload-bounded.
- No input or example accepts arbitrary SQL, a connection string, a database path, or an unrestricted relation.
- Dataset dependencies determine store routing.
- HTTP and in-process validation and results match before parity is claimed.
- Dashboard routes, forms, sort fields, filters, relation allowlists, and pagination reconcile with registered exposures.

### Atlas and analytical exports

- Export profiles resolve only declared datasets and explicit owner stores.
- JSON/JSONL work without optional dependencies; unavailable Parquet fails clearly and cannot change canonical state.
- Export reads read-only copies or online backups, never live writable handles.
- One cohort manifest records registry/dataset versions, per-store receipts, schema, hashes, counts, chunks, freshness, and generation time.
- Exact staging containment, full validation, semantic-change detection, and atomic promotion have destructive-path and failure-injection tests.
- Cross-store output never claims an atomic timestamp it cannot prove.
- Failed publication leaves the last validated Atlas snapshot unchanged.

## Questions to resolve before activation

These are activation blockers, not invitations to invent missing facts:

- the canonical registry filename and registry-path environment variable;
- byte-exact migration resources and checksums;
- the complete final input/output schemas and workload bounds for all recovered tools;
- exact scheduler wrapper artifacts and final calendars;
- the Atlas source/package lock and deployment receipt format; and
- which optional derived materializations, if any, warrant persistent tables rather than ephemeral results.
