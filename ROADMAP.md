# Quant Data Infrastructure Rebuild Roadmap

Status: Accepted
Planning baseline: 2026-08-09
Scope: accepted implementation sequencing; Stages 1 through 3 implemented on
2026-08-09, with Stages 2 and 3 independently verified

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
- Stage 0 is complete. The offline Stage 1 vertical slice and Stage 2
  four-store foundation are fixture-validated implementations; Stage 2 has
  also passed independent verification. The bounded offline Stage 3 market and
  macro scope is fixture-validated and independently verified. Stage 4 and
  later stages remain closed until separately and explicitly authorized.
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
| 2 | Four-store persistence foundation | No | Independently verified offline evidence |
| 3 | Bounded market and macro restoration | No; reviewed fixtures only | Independently verified offline evidence; Stage 4 requires separate authorization |
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

Implementation status: **Implemented**. Reproducible results and deterministic
receipts are recorded in the
[Stage 1 acceptance evidence](docs/rebuild/STAGE1_EVIDENCE.md).

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

Implementation status: **Implemented — fixture-validated**. The approved
offline evidence is recorded in [Stage 2 acceptance evidence](docs/rebuild/STAGE2_EVIDENCE.md).
It is a bounded foundation; Stage 3 required its own explicit authorization
and acceptance evidence.

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

### Recorded Stage 2 foundation

- Registry revision `2.0.0` fixes four distinct operational stores, ten
  fixture-validated store-local migrations, exactly 57 reserved compatibility
  names, and only the two validated public tools.
- The shared control plane covers artifacts, snapshots, quality results,
  ingestion-run outputs, and immutable failure audits. Company and news have
  only empty-domain foundations.
- The physical layer contract is exactly `evidence`, `canonical`, and
  `derived`. A forward-preserving Stage 2 table rebuild uses temporary
  foreign-key disablement only inside its atomic boundary and requires
  `foreign_key_check` before ledger advancement.
- Immutable identities resist `INSERT OR REPLACE` even with recursive triggers
  off; library connections enable recursive triggers, running-run identities
  and failed run IDs cannot be reused, and health reconciles exact reviewed
  SQLite schema SQL.
- Routing rejects unified and duplicate physical identities; locks use the
  resolved physical store identity and deterministic multi-lock order.
- Cross-store composition applies a declared `captured_at` cutoff and reports
  its honest `best_effort_multi_store` cohort semantics rather than claiming a
  cross-file atomic read.
- Read-only health reconciliation and WAL-safe SQLite online backup/restore
  prove equal source, backup, and restored logical evidence without mutating
  the source cohort.
- Live providers, scheduler installation or jobs, exports or Atlas, promotion,
  and destructive storage operations are excluded. The bounded Stage 3 fixture
  scope is separately recorded below.

### Exit gate

- Fresh and repeated initialization is idempotent for all four stores.
- Applied migration identities are immutable and verifiable.
- Store ownership agrees across migrations, registry, tools, dashboard,
  scheduler plans, and exports.
- Cross-store composition uses bounded application-level joins under one
  declared cutoff.

Stage 2 evidence records fixture validation and the independent verifier pass.
It did not by itself authorize Stage 3 executable work.

## 7. Stage 3 — Restore market and macro domains

Status: **Implemented — fixture-validated and independently verified.** The
user authorized this bounded offline fixture scope
after Stage 2. Its deterministic evidence is recorded in the
[Stage 3 acceptance evidence](docs/rebuild/STAGE3_EVIDENCE.md). This status
does not open Stage 4, live collection, jobs, exports, or new public tools.

### Fixture-validated scope

- Registry revision `2.1.0` declares 21 fixture-validated migrations, 19
  datasets, 14 collectors, the same two validated public tools, 57 reserved
  names, and no jobs or exports.
- Market adds canonical instrument/catalog evidence, dated provider identifiers,
  effective-dated classifications, controlled universe membership, complete
  scope tombstones and restoration, and exactly the nine recovered FMP index
  identities. The Stage 1 daily-price fixture remains preserved; no live price
  provider is restored.
- Macro adds a generic series catalog and observations, GDP vintage provenance,
  Treasury curves, an economic calendar, aggregate-only SOMA evidence and
  summaries, EIA electricity-retail and weekly-fundamentals contracts, and a
  completed U.S. recession chronology. BLS, BIS, Chicago Fed, and BEA fixtures
  exercise the generic semantic gate; they do not connect to providers.
- The gate imports 25 reviewed synthetic Stage 3 fixtures (29 including the
  preserved Stage 1 fixtures), rejects incomplete or internally inconsistent
  complete EIA retail captures without mutation, verifies volatile-only
  BLS/BEA metadata replays as no-ops, and compares source, backup, restored,
  and two-root path-free evidence.

### Market and macro contract coverage

1. Canonical instruments and dated provider identifiers.
2. Controlled universes and classifications.
3. Preserved daily-price evidence, canonical facts, correction history, and
   quality checks from the Stage 1 vertical slice.
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

The primary fixture gate and read-only SolUltra verification passed these
bounded checks and reproduced the acceptance evidence. That completes `G3` but
does not itself authorize Stage 4.

## 8. Stage 4 — Restore company, news, and options

Status: **Closed pending explicit authorization.** The Stage 3 `G3` gate has
passed, but no executable Stage 4 work is authorized by that result alone.

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

The shared visual and interaction language is defined in
[UI_VISUAL_DIRECTION.md](docs/rebuild/UI_VISUAL_DIRECTION.md). Stage 6 owns the
first implementation of these tokens and the common application shell.

### Deliverables

- ThesisTrade-inspired Inter typography, restrained institutional light
  palette, shared design tokens, and one persistent application shell.
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
- Visual-regression, responsive, keyboard, reduced-motion, contrast, and
  locally bundled font checks pass without any reference-site runtime asset.

## 11. Stage 7 — Restore scheduling and operations

The contract is defined in
[SCHEDULING_AND_LOCKING.md](docs/rebuild/SCHEDULING_AND_LOCKING.md).

Stage 2 provides only an offline, fixture-validated read-only health and
WAL-safe backup/restore foundation. Manual operational rehearsals, job
definitions, scheduler installation, and scheduler-owned receipts remain
Stage 7 work.

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

Atlas presentation also follows the shared [UI visual
direction](docs/rebuild/UI_VISUAL_DIRECTION.md); it does not create a separate
theme.

### Deliverables

- Benchmark evidence before introducing Parquet or DuckDB.
- Immutable Parquet snapshots only as derived, reproducible products.
- Optional DuckDB usage isolated from authoritative writes.
- Exact snapshot identity, registry version, code version, source-store
  receipts, and dataset ownership in every export.
- New staging directory, complete validation, semantic-change detection, and
  atomic promotion for Atlas.
- Atlas reuses the Stage 6 visual tokens and component language while exposing
  snapshot identity, freshness, provenance, and unavailable states explicitly.

### Exit gate

- Deleting derived exports never loses authoritative data.
- An export can be reproduced from declared source snapshots.
- Atlas cannot connect to or mutate operational stores.
- Partial multi-store exports cannot be promoted.
- Atlas visual fixtures and accessibility checks pass without remote fonts or
  reference-site assets.

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
