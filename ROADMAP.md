# Quant Data Infrastructure Rebuild Roadmap

Status: Accepted  
Planning baseline: 2026-08-09  
Scope: accepted implementation sequencing; Stage 1 authorized on 2026-08-09

## 1. Purpose

This roadmap translates the historical recovery evidence in [plan.md](plan.md)
into bounded implementation stages. It is intentionally narrower than the full
recovered feature inventory: the rebuild must prove its data contracts and
operational boundaries before restoring breadth.

The target architecture is defined in [ARCHITECTURE.md](ARCHITECTURE.md). Detailed
contracts and decisions are indexed in
[docs/rebuild/README.md](docs/rebuild/README.md).

## 2. Decisions and scope boundaries

The following decisions apply to every stage:

- Do not search for or reconstruct the lost Git history. When implementation
  begins, create a fresh repository baseline from the accepted planning
  documents.
- Stage 0 is complete and Stage 1 is authorized as an offline fixture-only
  implementation. Later stages remain gated and are not authorized by this
  status change.
- A dedicated provider-rights governance subsystem is outside this rebuild
  plan. Provider access and retention choices remain explicit implementation
  inputs rather than a new platform feature.
- SQLite remains the authoritative operational store unless benchmarks and an
  accepted decision record justify a change.
- The four operational boundaries are market, macro, company, and news.
- Immutable evidence, canonical versioned facts, and derived research outputs
  are separate layers.
- Point-in-time availability is a shared contract, not a domain-specific
  convention.
- The public 57-tool inventory is a compatibility target. Internal
  implementation should use a smaller composable core.
- Scheduling follows physical database paths and is restored only after manual
  workflows are deterministic and idempotent.

## 3. Stage overview

| Stage | Outcome | Live data allowed? | Exit decision |
| --- | --- | --- | --- |
| 0 | Planning package accepted | No | Architecture, ADRs, contracts, and scope are approved |
| 1 | One complete vertical slice | No; fixtures only | End-to-end contracts work on temporary stores |
| 2 | Four-store persistence foundation | No | All stores initialize and migrate independently |
| 3 | Market and macro restoration | Controlled only after offline gates | Identity, revision, and as-of behavior pass |
| 4 | Company, news, and options restoration | Controlled only after offline gates | Domain-specific immutable/versioned contracts pass |
| 5 | Composable 57-tool platform | No live access except separately approved intraday tool | Schemas, routing, limits, and strict JSON pass |
| 6 | Local portal and research surfaces | No mutation through UI | Read-only APIs and browser contracts pass |
| 7 | Scheduling and operational readiness | Dry-run first | Locking, retries, receipts, backups, and failure handling pass |
| 8 | Derived analytical exports and Atlas | Snapshot reads only | Atomic, reproducible publication passes |
| 9 | Controlled repopulation and promotion | Yes, explicitly approved | New stores pass integrity and recovery drills |

Stages are ordered by dependency, not calendar duration. A later stage may be
designed in parallel, but implementation does not cross an unmet exit gate.
The dependency-aware lanes, serialized hotspots, four-thread cadence, and
integration checkpoints are defined in the
[parallel execution plan](docs/rebuild/PARALLEL_EXECUTION.md).

## 4. Stage 0 — Accept the planning package

### Deliverables

- Accept or revise [ARCHITECTURE.md](ARCHITECTURE.md).
- Accept the foundational ADRs under [docs/adr](docs/adr).
- Review every focused specification under
  [docs/rebuild](docs/rebuild/README.md).
- Resolve terminology conflicts between the early recovery narrative and the
  superseding evidence in Sections 18–20 of `plan.md`.
- Classify unresolved details as required before the vertical slice, required
  before a later domain stage, or safe to defer.

### Exit gate

- Every planned component has one owner and one authoritative document.
- The vertical-slice scope contains no unresolved semantic decision.
- ADR statuses move from `Proposed` to `Accepted` only with user approval.
- A fresh Git repository may then be initialized; no lost history is inferred.

## 5. Stage 1 — Prove one complete vertical slice

The first implementation slice is defined in
[VERTICAL_SLICE_SPEC.md](docs/rebuild/VERTICAL_SLICE_SPEC.md). It deliberately
includes both current-state market data and revision-aware macro data so the
architecture cannot pass by implementing only the simpler case.

### Deliverables

- A minimal registry containing only the slice's datasets, stores, migrations,
  tool exposure, and quality rules.
- Temporary market and macro SQLite stores with independent migration ledgers.
- One daily-price fixture family, including an exact replay and correction.
- One macro-vintage fixture family, including first, revised, and as-of states.
- Immutable evidence records and canonical facts linked by stable identity.
- Shared availability and missingness semantics.
- A minimal read-only tool path using shared primitives.
- A minimal local inspection page or endpoint using the same read path.
- Deterministic clean rebuild and test commands.

### Exit gate

- No test can fall back to a default or live database path.
- Exact replay is a no-op; correction and revision behavior are explicit.
- An as-of cutoff cannot see evidence captured later.
- Strict JSON, resource limits, read-only routing, and error envelopes pass.
- Destroying and recreating temporary stores yields the same logical result.
- The architecture review finds no special-case shortcut that would fail when
  a third domain is added.

## 6. Stage 2 — Establish the four-store foundation

### Deliverables

- Independent market, macro, company, and news initialization.
- Shared control-plane conventions for migrations, dataset registration,
  ingestion runs, artifacts, snapshots, and quality results.
- One accepted machine-readable registry as the source of store ownership and
  cross-cutting configuration.
- Registry validation that rejects duplicate IDs, unknown stores, migration
  collisions, mutable identity fields, and unsafe routing.
- Four distinct operational stores only. Unified runtime compatibility is
  retired; duplicate physical store identities fail closed.
- Read-only connection factories and host-selected database routing.

### Exit gate

- Fresh and repeated initialization is idempotent for all four stores.
- Applied migration identities are immutable and verifiable.
- Store ownership agrees across migrations, registry, tools, dashboard,
  scheduler plans, and exports.
- Cross-store composition uses bounded application-level joins under one
  declared cutoff.

## 7. Stage 3 — Restore market and macro domains

### Market sequence

1. Canonical instruments and dated provider identifiers.
2. Controlled universes and classifications.
3. Daily price evidence, canonical facts, correction history, and quality
   checks.
4. The nine recovered FMP index identities.
5. Effective-dated universe membership before survivorship-sensitive
   cross-sectional research is presented as safe.

### Macro sequence

1. Canonical series catalog and dimensions.
2. Versioned observations, releases, artifacts, and snapshot membership.
3. RTDSM and GDP vintage behavior.
4. Treasury, funding, liquidity, and recession chronology.
5. BLS, BIS, Chicago Fed, BEA, SOMA, and EIA families.

### Exit gate

- Identifier changes do not change canonical identity.
- Latest, as-of, and defensible first-release fixtures differ as expected.
- Date-only values do not acquire invented timestamps.
- Corrections, partial captures, missing values, tombstones, and restoration
  are all fixture-tested.
- No CUSIP-level SOMA storage or source path exists.

## 8. Stage 4 — Restore company, news, and options

### Company

- CIK-based issuer identity and dated security/provider links.
- Immutable SEC accessions and captured CompanyFacts.
- Versioned normalized fundamentals with mapping-version lineage.
- Separate instant and weighted-average share semantics.
- Versioned actions, expectations, earnings events, and guidance.

### News

- Immutable logical items, raw evidence, changed-content versions, snapshot
  membership, labels, retractions, and point-in-time search.
- FTS remains a derived index over canonical content, not the source of truth.

### Options

- The recovered migration 0029/0030 domain model.
- One immutable capture, underlying, environment, and resolved feed per
  surface.
- Explicit missing contracts and a complete exclusion ledger.
- Synchronized underlying, rate, dividend, and expiry inputs.

### Exit gate

- Company ticker changes and joint filings preserve issuer/accession identity.
- A news as-of query excludes later local captures.
- Option analysis cannot combine capture IDs, feeds, environments, or
  nonstandard deliverables silently.
- All domain tests use temporary stores and redacted fixtures.

## 9. Stage 5 — Restore the composable tool platform

The contract is defined in
[TOOL_PLATFORM_SPEC.md](docs/rebuild/TOOL_PLATFORM_SPEC.md).

### Deliverables

- Shared typed primitives for discovery, retrieval, transformation, alignment,
  statistics, econometrics, research contracts, and strict serialization.
- Public compatibility adapters for the reviewed 57 tool names.
- Registry-derived input/output schemas, examples, limits, and routing.
- Tool-version and data-lineage metadata in every research response.
- Additive compatibility and explicit deprecation rules.

### Exit gate

- Every public schema rejects unknown properties.
- Caller input cannot choose database paths or submit SQL.
- Equivalent HTTP and in-process calls produce equivalent contracts.
- Every limit is enforced before expensive work begins.
- Results are deterministic, read-only, finite JSON with explicit exclusions
  and warnings.

## 10. Stage 6 — Restore portal and research surfaces

### Deliverables

- Overview, GDP Vintages, Tables, and Agent Tools first.
- Charts and shared indicator research after tool contracts stabilize.
- Algorithms and walk-forward reports after causal timing and cost contracts
  stabilize.
- Persistent navigation and recovered security headers.
- Strict separation between operational inspection and derived research views.

### Exit gate

- The browser has no ingestion or raw-SQL capability.
- All database connections are read-only and query-only.
- Warm-up and missing observations render as gaps rather than zero.
- Indicator calculations have golden vectors and one canonical backend.
- Backtests use training-only transformations, explicit decision/execution
  times, costs, benchmarks, and deterministic ties.

## 11. Stage 7 — Restore scheduling and operations

The contract is defined in
[SCHEDULING_AND_LOCKING.md](docs/rebuild/SCHEDULING_AND_LOCKING.md).

### Deliverables

- Manual commands proven before job definitions are enabled.
- Physical-path-derived locks with deterministic multi-lock acquisition.
- Correct behavior for four distinct split stores; no unified runtime fallback.
- Dry-run plans, bounded retries, `if-new` semantic gates, receipts, private
  logs, and nonzero aggregate failure status.
- Online SQLite backup and restore procedures tested against copies.

### Exit gate

- Concurrent-job tests prove that two logical domains targeting one physical
  file cannot write concurrently.
- Multi-store jobs acquire every required lock in deterministic order.
- Network work occurs outside write transactions.
- Unchanged releases create no run, artifact, snapshot, or membership writes.
- Partial and failed work cannot advance successful checkpoints.

## 12. Stage 8 — Add derived analytical exports and Atlas

The contract is defined in
[ANALYTICAL_EXPORTS.md](docs/rebuild/ANALYTICAL_EXPORTS.md).

### Deliverables

- Benchmark evidence before introducing Parquet or DuckDB.
- Immutable Parquet snapshots only as derived, reproducible products.
- Optional DuckDB usage isolated from authoritative writes.
- Exact snapshot identity, registry version, code version, source-store
  receipts, and dataset ownership in every export.
- New staging directory, complete validation, semantic-change detection, and
  atomic promotion for Atlas.

### Exit gate

- Deleting derived exports never loses authoritative data.
- An export can be reproduced from declared source snapshots.
- Atlas cannot connect to or mutate operational stores.
- Partial multi-store exports cannot be promoted.

## 13. Stage 9 — Controlled repopulation and promotion

### Deliverables

- Provider-specific bounded backfills into new, explicitly named non-production
  stores.
- Coverage, row-count, sample-value, gap, and provenance reconciliation.
- Integrity checks against read-only backup copies.
- Promotion receipts tying code, registry, migrations, store snapshots, and
  configuration together.

### Exit gate

- Every store passes integrity, foreign-key, migration, and dataset-quality
  checks.
- Idempotent replay and correction tests pass against production-shaped copies.
- Point-in-time and capture-boundary audits pass.
- Backup and clean restoration are demonstrated before any old store is
  retired.

## 14. Cross-stage quality gates

Every implementation change must answer:

1. What stable identity does this introduce or depend on?
2. What evidence proves when the value became available?
3. Is the mutation current-state, versioned, immutable, or tombstone-based?
4. Which physical store owns it?
5. Which registry entry, migration, tool, dashboard view, scheduler job, and
   export depends on it?
6. Can exact replay occur without changing canonical state?
7. Can a temporary-store test prove failure without touching a live path?
8. Can a research result cite exact data, code, parameters, and exclusions?

## 15. Deferred scope

The rebuild does not include:

- order, portfolio, or execution management;
- public or multi-user exposure of the local portal;
- tick-level or full intraday market infrastructure;
- automatic FX conversion or curve interpolation;
- CUSIP-level SOMA holdings;
- silent imputation or hidden composite regime scores;
- Phillips–Perron until a validated numerical dependency is accepted; or
- a provider-rights governance subsystem.

## 16. Roadmap maintenance

- Roadmap stages describe sequence and gates, not detailed contracts.
- Contract changes belong in the relevant specification and ADR first.
- A stage moves to `In progress` only after its dependencies and owned paths are
  explicit.
- A stage moves to `Complete` only when its exit gate has recorded evidence.
- Historical recovery evidence remains in `plan.md`; do not rewrite remembered
  history to make later implementation look inevitable.
