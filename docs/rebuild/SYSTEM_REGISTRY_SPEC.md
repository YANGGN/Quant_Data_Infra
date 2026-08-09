# System Registry Specification

## Status

**Accepted.** The canonical registry path is `config/system_registry.json` and the optional host override is `QUANT_SYSTEM_REGISTRY_PATH`. Stage 1 may implement the accepted subset; a listed consumer exists only when executable evidence passes.

This specification elaborates [ADR 0002](../adr/0002-single-machine-readable-registry.md) and the [target architecture](../../ARCHITECTURE.md). Store and layer semantics are defined by [ADR 0001](../adr/0001-four-operational-sqlite-stores.md) and [ADR 0003](../adr/0003-three-layer-data-architecture.md). Recovery constraints come from the [rebuild plan](../../plan.md), with its Sections 18–20 taking precedence where earlier sections conflict.

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
| market | data/market_data.sqlite | QUANT_MARKET_DB_PATH |
| macro | data/macro_data.sqlite | QUANT_MACRO_DB_PATH |
| company | data/company_data.sqlite | QUANT_COMPANY_DB_PATH |
| news | data/news_data.sqlite | QUANT_NEWS_DB_PATH |

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
| datasets | array of Dataset IDs | Yes | Direct data dependencies |
| tools | array of Tool IDs | Yes | Guided tool forms available on the exposure |
| relations | array of allowlisted relation IDs | Yes | No caller-created table name |
| filters | strict schema | Yes | Bound fields and values |
| sort_fields | finite array | Yes | Server-owned allowlist |
| pagination | object | Yes | Default and hard maximum |
| visibility | enum | Yes | local_private |

Dashboard navigation and the table inspector are generated or validated from these exposures. Internal raw payload objects remain absent unless a reviewed exposure explicitly permits them.

### Export

| Field | Type | Required | Contract |
| --- | --- | --- | --- |
| id | namespaced identifier | Yes | Stable export profile |
| version | semantic version | Yes | Output contract version |
| kind | enum | Yes | analytical_file or atlas_snapshot |
| format | enum | Yes | json, jsonl, or optional parquet |
| datasets | non-empty array of Dataset IDs | Yes | All exported datasets and their owners |
| query_contract | object | Yes | Columns, ordering, filters, caps, and point-in-time policy |
| schema_contract | object | Yes | Output schema version and null/number handling |
| chunking | object | Yes | Deterministic boundaries and maximum sizes |
| freshness | object | Yes | Snapshot health derived from dataset freshness |
| source_mode | enum | Yes | read_only_copy or online_backup |
| consistency | object | Yes | Cohort receipts and cross-store limitations |
| staging | object | Yes | Exact parent, generated child, validation, and atomic promotion |
| optional_dependency | string/null | Yes | Parquet adapter name when used; never silently required by the core |

JSON and JSONL are supported by the standard-library core. Parquet is optional and must fail with a clear unavailable-capability error when its declared adapter is absent. Export format does not alter canonical storage.

An Atlas export reads only registered exportable datasets from explicit read-only store copies. Its manifest records registry version, dataset contract versions, per-store backup or read-copy receipts, row/chunk counts, schemas, freshness, hashes, generation time, and source revision. The exporter never labels a multi-store cohort as one atomic database timestamp.

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
17. Atlas profiles export only declared datasets, stage inside an exact parent, validate the entire cohort, and promote atomically.
18. Unsupported layers, formats, schema versions, status values, and optional capabilities are rejected rather than guessed.
19. Secrets and secret values do not appear anywhere in the registry, examples, generated documentation, logs, or exported manifests.
20. Registry iteration and generated output are deterministic across processes and platforms.

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
| Dashboard | Dashboard exposures, tools, relation allowlists | Generate or validate navigation/forms and bounded inspection |
| Atlas exporter | Export profiles, dataset ownership, store paths | Read copies, stage exactly, validate cohort, atomically promote |
| Documentation generator | Public metadata only | Produce deterministic reference pages without secrets or runtime claims |

## Generation policy

Generated artifacts may include:

- store/dataset ownership maps;
- migration plans;
- typed identifier constants;
- tool discovery documents and guided forms;
- dashboard navigation and inspector allowlists;
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
    default_path: data/market_data.sqlite
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
