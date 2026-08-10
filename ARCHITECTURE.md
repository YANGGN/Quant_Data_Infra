# Quant Data Infrastructure Architecture

Status: Accepted

## Status and scope

This document defines the accepted target architecture for the clean rebuild described in the [rebuild plan](plan.md). The offline Stage 1 vertical slice and independently verified Stage 2 four-store foundation are implemented with fixture-validated evidence; Stage 3 remains gated and unauthorized. A contract is implemented only after its executable acceptance evidence passes. Later recovery evidence in Sections 18–20 of the plan takes precedence over earlier proposals.

The principal decisions are recorded in:

- [ADR 0001: Four operational SQLite stores](docs/adr/0001-four-operational-sqlite-stores.md)
- [ADR 0002: One machine-readable system registry](docs/adr/0002-single-machine-readable-registry.md)
- [ADR 0003: Three-layer data architecture](docs/adr/0003-three-layer-data-architecture.md)
- [ADR 0008: Fresh store-local reconstruction migrations](docs/adr/0008-fresh-store-local-reconstruction-migrations.md)
- [System registry specification](docs/rebuild/SYSTEM_REGISTRY_SPEC.md)

## Context

Quant Data Infrastructure is a local, provider-neutral research platform. It ingests repeatable market, macroeconomic, company, options, energy, rates, and news data; preserves provenance and availability; and exposes bounded read-only services to people and analytical agents.

The rebuild targets Python 3.11 or later, SQLite through the standard-library sqlite3 module, and a standard-library-heavy analytical and service core. Optional dependencies may support isolated features, but the operational foundation must not depend on a large framework.

### Goals

- Preserve source evidence and enough temporal metadata for defensible point-in-time research.
- Maintain stable canonical identities and append corrections or revisions instead of rewriting history.
- Isolate operational writes, locks, migrations, backup, and failure by domain.
- Give tools and the local dashboard fixed, validated, read-only interfaces.
- Make every fill, transformation, exclusion, aggregation, and availability choice visible to the caller.
- Publish Atlas only from validated, read-only analytical snapshots.
- Keep configuration, ownership, migrations, freshness, exposure, and export intent consistent through one machine-readable registry.

### Non-goals

- Order execution, portfolio or order management, and brokerage state.
- Tick-level or real-time market-data infrastructure.
- A public, multi-user, or multi-tenant database service.
- Automatic currency conversion, curve interpolation, hidden imputation, or opaque composite scores.
- CUSIP-level SOMA holdings.
- Direct database access from Atlas, the dashboard, or public tools.
- Treating a surviving frontend cache as proof of a framework or deployable application.

## Architectural principles

1. **Evidence before interpretation.** Source artifacts, request scope, hashes, capture time, and availability are retained before canonical facts are derived.
2. **One owner per dataset.** Every registered dataset has exactly one operational store and one declared architectural layer.
3. **Append facts; do not erase history.** New evidence may append a canonical version, correction, retraction, or capture. It does not silently overwrite the earlier state.
4. **Source-native time.** Date-only values remain dates, offset-bearing timestamps retain their source representation, and no timezone precision is invented.
5. **Read interfaces are bounded and explicit.** No raw SQL, caller-selected database path, unbounded search, or implicit transformation crosses a public boundary.
6. **Physical resources determine coordination.** Locks and backup boundaries follow the resolved SQLite file, not merely a logical store name.
7. **Derived products are consumers.** Research results, caches, reports, and Atlas snapshots never become untracked canonical inputs.

## Component flow and boundaries

    Provider APIs and controlled inputs
                    |
            source-specific adapters
                    |
        fetch into an ephemeral buffer
                    |
       semantic identity / no-write gate
                    |
      evidence capture and normalization
                    |
       short transactional store writer
                    |
       four operational SQLite stores
                    |
          provider-neutral read services
              /                 \
     fixed read-only tools    local dashboard
              \                 /
            derived research services
                       |
          read-only Atlas snapshot exporter
                       |
             private static Atlas site

| Component | Responsibility | Write authority |
| --- | --- | --- |
| Adapter | Fetch one source contract, validate transport and schema, compute source-specific semantic identity | Ephemeral fetch buffer only |
| Ingestion service | Normalize evidence and facts, enforce idempotence, acquire physical locks, and commit short transactions | Declared output datasets in declared stores |
| Scheduler or manual job runner | Orchestrate collectors, calendars, retries, and aggregate exit status | None directly |
| Operational stores | Hold evidence, canonical facts, local migration state, dataset state, and ingestion outcomes | Ingestion and migration paths only |
| Read/query service | Apply allowlisted queries, temporal rules, pagination, and workload bounds | None |
| Analytical service | Transform typed in-memory series and produce audited derived results | None, unless a separately registered derived materialization is invoked |
| Local dashboard | Inspect live operational state through fixed read services | None |
| Atlas exporter | Build a staged, validated analytical snapshot from read-only store copies | Exact staging directory only |
| Atlas site | Explore a published static snapshot | None |
| System registry | Declare ownership and contracts for all of the above | Changed only through reviewed configuration lifecycle |

Network fetches occur outside SQLite transactions. A collector opens a write transaction only after transport, schema, semantic identity, and batch validation succeed.

## Operational stores

The target has four operational SQLite stores. These are write and failure boundaries, not merely naming conventions.

| Store | Default path | Environment override | Primary ownership |
| --- | --- | --- | --- |
| Market | data/market_data.sqlite | QUANT_MARKET_DB_PATH | Instruments, dated identifiers, classifications, daily prices and versions, price requests, option contracts and immutable option captures |
| Macro | data/macro_data.sqlite | QUANT_MACRO_DB_PATH | Calendar, macro catalogs and versioned observations, GDP vintages, Treasury curves, funding and liquidity, recession chronology, SOMA summaries, and EIA facts |
| Company | data/company_data.sqlite | QUANT_COMPANY_DB_PATH | SEC issuers, submissions, filings, facts, normalized fundamentals, corporate actions, share history, earnings events, consensus, and guidance |
| News | data/news_data.sqlite | QUANT_NEWS_DB_PATH | Immutable source artifacts, logical items and changed-content versions, capture membership, labels, retractions, and search indexes |

Options belong to the market store. ETF, equity, and index prices share one instrument model and one daily-price fact family; asset type is metadata, not a reason to create a table per symbol or asset class.

The former unified `data/quant_data.sqlite` path and `QUANT_DB_PATH` are retired as runtime compatibility. The rebuilt runtime supports exactly four distinct operational stores and never falls back to a unified file. A future one-shot legacy import utility would require separate design and authorization and would not be an operational mode.

Each operational store owns:

- its schema migration ledger;
- its applied dataset registry and ingestion-run state;
- its WAL, foreign-key, busy-timeout, and integrity policy;
- one physical write lock and backup boundary;
- read-only routing for the datasets it owns; and
- independent initialization, health, backup, restore, and failure tests.

SQLite cannot enforce foreign keys across files. Cross-store relationships therefore use stable application identities such as instrument_id, SEC issuer identity, series_id, option capture identity, and news item identity.

## Three data layers

The layers are logical contracts. They can coexist in one domain store; they are not three additional databases.

### 1. Immutable evidence

Evidence records what was fetched or observed and under what scope. It includes source artifacts or content hashes, sanitized request metadata, raw source timestamps and precision, capture/snapshot identity, snapshot membership, fetch status, and provenance. Option surface captures and changed-content news captures are evidence even when they also expose useful query fields.

An unchanged semantic response is detected before persistent evidence is created. For collectors with an if-new gate, unchanged means zero ingestion-run, artifact, snapshot, and membership writes.

### 2. Canonical versioned facts

Canonical facts provide stable, provider-neutral identities and explicit version semantics. Examples include instruments and dated identifiers, prices_daily and price corrections, macro series and observation versions, issuer and filing facts, corporate-action versions, and news item versions.

Canonical rows carry the source evidence needed to explain them. A correction, revision, retraction, or changed classification appends a new version where the source semantics support history. Identical replay is a no-op.

### 3. Derived research

Derived research includes returns, alignments, descriptive statistics, regressions, indicators, event studies, walk-forward reports, caches, and Atlas exports. Its inputs, availability cutoff, transformation policy, exclusions, warnings, and truncation must be recorded with the result.

Derived output may be ephemeral or materialized into a registry-declared derived dataset. It cannot silently mutate evidence or canonical facts, and it cannot be presented as point-in-time safe unless every input was eligible at the declared cutoff.

See [ADR 0003](docs/adr/0003-three-layer-data-architecture.md) for consequences and rejected alternatives.

## Store-local control plane

Every Stage 2 store carries the same ten control-plane relations:

- **schema_migrations** records ordered, immutable applied migrations and their
  checksums;
- **dataset_registry** and **dataset_identity_contracts** record the store-local
  applied state and immutable identity contract of datasets owned by that store;
- **ingestion_runs**, **ingestion_run_outputs**, and
  **ingestion_run_failures** record durable work outcomes, declared outputs,
  and immutable failure audits;
- **ingestion_artifacts**, **ingestion_snapshots**, and
  **ingestion_snapshot_artifacts** retain immutable evidence and its snapshot
  lineage; and
- **data_quality_results** retains immutable quality outcomes tied to the
  published work.

The system registry is declarative source configuration; the implemented Stage 2
revision is `2.0.0`. The store-local dataset registry is runtime evidence.
Neither replaces the other. Startup and health checks reconcile them and fail
closed on missing ownership, an unexpected migration state, or a dataset mapped
to the wrong physical store.

The physical dataset-layer contract is exactly `evidence`, `canonical`, and
`derived`. The Stage 2 forward-preserving table rebuild aligns the SQLite
constraint to that contract. It may disable foreign-key enforcement only inside
the atomic rebuild, and must pass `foreign_key_check` before the migration
ledger advances. Immutable control-plane identities reject `INSERT OR REPLACE`
even if recursive triggers are off; library connections enable recursive
triggers as an additional defense. A running run's identity is immutable, and
a failed run ID is durable audit evidence that cannot be reused by a later
failed or successful attempt.

Applied migration SQL is immutable. A correction is a new migration. The ten
currently allocated Stage 1 and Stage 2 resources are fixture-validated
deliberate reconstructions, never claims of `recovered_exact` parity. The
recovered semantic migration sequence runs through 0031, but byte-exact DDL,
checksums, indexes, and triggers beyond the reviewed resources remain evidence
that must be reconstructed and fixture-tested before parity is asserted. In
particular, the filing/issuer membership object associated with migration 0031
is a derived view with supporting invariants, not a guessed physical join table.

Run status distinguishes succeeded, partial, failed, and unchanged behavior. External scheduler receipts may record a poll that made no database writes; they must not manufacture an ingestion run merely to record an unchanged release.

## Identity, time, and point-in-time composition

Provider ticker or symbol is a dated identifier, not a permanent company identity. Cross-store joins use canonical identities and explicit mapping evidence. Same-date ambiguity fails closed unless the caller supplies a stable event key.

Calendar dates are stored as ISO YYYY-MM-DD text. Real timestamps retain an offset. Date-only values do not acquire midnight or UTC. Start and end filters are inclusive calendar-date comparisons.

Canonical queries expose only supported vintage modes:

- **latest** selects the newest stored version for each reference period;
- **as_of** selects the newest version actually available no later than an explicit date or offset-aware timestamp; and
- **first_release** selects the earliest defensible source release, not merely the first local capture.

Sources with only current-state captures must expose local-capture semantics or reject unsupported historical queries.

Cross-store composition follows this sequence:

1. Resolve each dataset through the system registry.
2. Open every required store read-only and set query_only.
3. Apply one explicit availability boundary to every input.
4. Fetch bounded, deterministically ordered result sets.
5. Join in application memory using stable identities.
6. Return provenance, exclusions, missingness, warnings, and truncation with the result.

The implemented Stage 2 foundation composes bounded capture/snapshot evidence
only when a dataset declares `captured_at` as its availability field; it never
substitutes ingestion completion time. Each contributing store is read under a
SQLite read transaction with start and completion state receipts. There is no
claim of one atomic transaction across stores: the result explicitly reports
`best_effort_multi_store` consistency and `none_best_effort` cohort evidence,
along with the contributing store receipts.

## Ingestion and mutation path

1. Resolve the collector, output datasets, store paths, and physical locks from the registry.
2. Fetch outside a transaction into a bounded ephemeral buffer.
3. Validate status, schema, request scope, identities, temporal precision, and domain constraints.
4. Compute the source-specific semantic identity. A partial response, timeout, rate limit, or schema error is never evidence of no change and never grounds for a tombstone.
5. If unchanged, emit an external no-write receipt and stop without persistent store changes.
6. Stage and normalize changed evidence, canonical rows, and quality findings.
7. Acquire locks in deterministic physical-path order.
8. Open short transactions with foreign keys enabled, a bounded busy timeout, bound parameters, and WAL where supported.
9. Commit evidence, canonical versions, snapshot membership, checkpoints, and run outcome atomically for each logical batch.
10. Roll back the logical batch on validation or write failure; release locks; return a nonzero or incomplete aggregate result when any required item fails.

Independent collector steps may continue after another step fails, but successful substeps and failures remain separately visible. Retries target transient failed items only. Authentication and schema failures stop rather than loop indefinitely.

Domain validation includes OHLC consistency, nonnegative volume, duplicate natural keys, sorted dates, unit and scale consistency, explicit missing reasons, capture/feed isolation for options, and retraction/version handling for news. No write path silently forward-fills, resamples, converts currency, interpolates curves, mixes price variants, combines instant and average shares, or mixes option captures.

## Read paths and public boundaries

Dashboard and tool connections use SQLite URI read-only mode, set PRAGMA query_only = ON, use bound parameters, and route through fixed queries or service functions. Caller input never selects SQL, a table outside an allowlist, or a database path.

At Stage 2, the only validated public tool names are `macro.get_series` and
`timeseries.describe`; all 57 compatibility names remain reserved, with no
other validated public tool at this stage. Read-only health reconciles every
explicit store against the registry without initializing or mutating a store.
It compares exact reviewed `sqlite_master` schema SQL, not relation and trigger
names alone.

Tool contracts are versioned, deterministic, strict JSON:

- every object schema rejects undeclared properties;
- request and result sizes are bounded;
- pagination, sort fields, filters, matrices, lags, rolling windows, and output points have schema-visible limits;
- NaN and Infinity never cross the JSON boundary;
- data, provenance, quality warnings, exclusions, and truncation are distinct fields; and
- composable analytical tools accept typed in-memory series rather than hidden database handles.

The local dashboard uses the same read services. Its table inspector exposes only server-owned table/view allowlists and bounded filters. Health shows each store independently and does not convert a missing store into a unified-path fallback.

## Local dashboard and Atlas

| Concern | Local dashboard | Quant Data Atlas |
| --- | --- | --- |
| Purpose | Inspect current operational stores, ingestion state, tools, charts, and research | Explore a curated analytical snapshot |
| Data access | Fixed live read services over read-only SQLite connections | Static manifest and chunked export files |
| Write access | None | None to operational stores |
| Consistency | Per-query store receipts and explicit availability cutoff | Declared multi-store snapshot cohort; never a falsely unified database as-of |
| Deployment | Local host, private to the workstation | Separate private static deployment |
| Failure impact | A failed domain degrades only its routes and health | A failed export leaves the last validated snapshot in place |

Atlas is a derived snapshot consumer, not the canonical portal and not a live SQLite client. Publication requires:

1. collector-completion receipts and a non-overlap export lock;
2. explicit dataset ownership and explicit read-only store copies or online backups;
3. a newly created, exactly resolved staging directory inside its designated parent;
4. deterministic export ordering and one versioned snapshot manifest;
5. complete chunk, schema, count, freshness, and referential validation;
6. semantic-change detection;
7. atomic promotion only after the full cohort validates; and
8. a deployment receipt tying the snapshot to one source revision and registry version.

The exporter must never recursively clean an empty, unresolved, broad, repository-root, home, or volume path. Failure before promotion preserves the previous Atlas snapshot.

## Deployment modes

The implemented Stage 2 boundary is offline only. It does not install or run
scheduler jobs, fetch live providers, create exports or Atlas snapshots,
promote data, perform destructive storage operations, or restore the broader
Stage 3 market and macro domains.

### Isolated development and test

All four paths are explicitly redirected to temporary locations. Tests must never fall back to live or default databases. Migrations, collectors, point-in-time queries, failure cases, backup, and restore run against fixtures.

### Local operational

Four split files use their default paths or explicit environment overrides. Manual, idempotent jobs are restored before scheduled automation. The local dashboard and tool server bind to a local interface and open stores read-only.

### Scheduled local collection

The scheduler invokes named jobs rather than embedding collector logic. Per-job and per-physical-database locks prevent overlap. Calendars, retries, aggregate exit status, bounded private logs, and external no-write receipts are tested before installation.

### Atlas snapshot publication

Publication runs separately from collection. It uses explicit store paths, read-only copies, exact staging, validation, atomic promotion, and a private deployment target.

### Legacy import or rollback

A legacy unified file is not a supported runtime layout and cannot be auto-discovered as a substitute for a missing split store. Any future one-shot import is outside the current implementation scope.

## Failure isolation and recovery

- A market write failure does not roll back an already committed macro batch.
- Store health is reported independently; cross-store tools fail with a structured list of unavailable inputs.
- Lock ordering is deterministic by canonical physical path to prevent deadlock.
- If two logical store declarations resolve to one physical file, configuration validation fails closed; physical identity checks prevent the alias from bypassing coordination before rejection.
- A collector does not hold a database lock during network I/O or retry delay.
- Partial, stale, and truncated results are explicit states, never silent success.
- The Stage 2 foundation uses WAL-safe SQLite online backup rather than copying
  a live database file, and verifies source, backup, and restored logical
  evidence without mutating the source or backup cohort.
- Restore drills validate integrity, migration state, dataset ownership, and representative point-in-time queries on the restored copy.
- Atlas staging or deployment failure cannot modify the last promoted snapshot.

## Quality attributes

| Attribute | Architectural requirement |
| --- | --- |
| Correctness | Stable identities, explicit units, source precision, version semantics, deterministic ordering, and domain invariants |
| Point-in-time safety | Availability metadata and one explicit cutoff across every composed input |
| Idempotence | Exact replay and semantically unchanged releases produce no canonical change; if-new collectors produce no database writes |
| Auditability | Every canonical version traces to evidence, collector contract, code/schema version, and run outcome |
| Isolation | Four store, lock, migration, backup, health, and failure boundaries |
| Safety | Read-only consumers, bound SQL, strict schemas, exact filesystem targets, and secrets excluded from persisted metadata |
| Performance | Short transactions, bounded queries, pagination, workload limits, WAL where supported, and in-memory joins only for bounded sets |
| Portability | Python 3.11+, sqlite3, source-native ISO values, relative defaults, and explicit environment overrides |
| Recoverability | Online backups, integrity checks, migration reconciliation, restore drills, and atomic Atlas promotion |
| Evolvability | Versioned registry, additive migrations, versioned tool contracts, and optional adapters isolated from the core |

Historical row counts and old test totals are diagnostic checkpoints, not targets. Acceptance is based on restored behavior, invariants, fixtures, and evidence.

## Known reconstruction boundaries

The architecture does not invent byte-exact migration SQL, migration checksums, unverified indexes or triggers, the complete final tool schemas, scheduler XML, or the Atlas source lockfile. Those artifacts require evidence and explicit review before a parity claim.

The recovered physical names and semantic migration order in plan Sections 20.1–20.5 constrain reconstruction. Where validated executable evidence later conflicts with this document, record the conflict and update the applicable ADR or specification rather than silently changing behavior.

## Decision map

- Store topology and operational isolation: [ADR 0001](docs/adr/0001-four-operational-sqlite-stores.md)
- Registry ownership and consumers: [ADR 0002](docs/adr/0002-single-machine-readable-registry.md)
- Evidence, fact, and research layering: [ADR 0003](docs/adr/0003-three-layer-data-architecture.md)
- Point-in-time availability and revisions: [ADR 0004](docs/adr/0004-point-in-time-availability-model.md)
- Composable typed tool core and public compatibility: [ADR 0005](docs/adr/0005-composable-tool-core.md)
- Concurrency locks resolved by physical store: [ADR 0006](docs/adr/0006-lock-by-physical-store.md)
- SQLite authority and derived analytical exports: [ADR 0007](docs/adr/0007-sqlite-authority-derived-parquet.md)
- Registry fields, invariants, lifecycle, and acceptance: [system registry specification](docs/rebuild/SYSTEM_REGISTRY_SPEC.md)
- Temporal semantics and query modes: [data and time contracts](docs/rebuild/DATA_AND_TIME_CONTRACTS.md)
- First end-to-end proof: [vertical slice specification](docs/rebuild/VERTICAL_SLICE_SPEC.md)
- Public tool inventory and shared primitives: [tool platform specification](docs/rebuild/TOOL_PLATFORM_SPEC.md)
- Job calendars, overlap prevention, and exit semantics: [scheduling and locking](docs/rebuild/SCHEDULING_AND_LOCKING.md)
- Parquet, DuckDB, and Atlas publication boundaries: [analytical exports](docs/rebuild/ANALYTICAL_EXPORTS.md)
- Test layers, fixtures, and acceptance gates: [test strategy](docs/rebuild/TEST_STRATEGY.md)
- Delivery sequence and exit gates: [roadmap](ROADMAP.md)
