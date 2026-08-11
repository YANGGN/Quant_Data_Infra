# Quant Data Infrastructure Rebuild Roadmap

Status: Accepted
Planning baseline: 2026-08-09
Scope: accepted implementation sequencing; Stages 1 through 5 are
independently verified; the bounded Stage 6 four-route portal was accepted on
2026-08-10 with an explicit browser-automation waiver; the bounded offline
Stage 7 manual fixture rehearsal passed its primary gate and independent
SolUltra verification on 2026-08-11

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
- Stage 0 is complete. Stages 1 through 5 are fixture-validated and
  independently verified. The bounded offline Stage 6 four-route local portal
  passed its executable checks and was explicitly accepted with browser
  automation waived. The bounded offline Stage 7 manual fixture rehearsal has
  passed its primary gate and independent SolUltra verification.
  Stage 8 remains closed.
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
| 4 | Company, news, and options restoration | No; synthetic fixtures only | Independently verified offline evidence; Stage 5 is next |
| 5 | Composable 57-tool platform | No live access; intraday remains disabled | Independently verified offline evidence; bounded Stage 6 portal primary gate passed |
| 6 | Bounded local portal (four fixed routes) | No mutation through UI | Accepted offline evidence with explicit browser-automation waiver |
| 7 | Manual fixture job rehearsal and operational readiness | No live access; jobs disabled | Independently verified offline evidence; Stage 8 remains closed |
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

Status: **Implemented, fixture-validated, and independently verified.** The
user authorized this bounded synthetic fixture scope after `G3`. Its
deterministic receipts and verifier result are recorded in the
[Stage 4 acceptance evidence](docs/rebuild/STAGE4_EVIDENCE.md). `G4` is
complete. Stage 5 subsequently passed its primary offline fixture gate.

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

### Fixture-validated scope

- Registry revision `2.2.0` declares 30 fixture-validated migrations, 33
  datasets, 19 collectors, the same two validated public tools, 57 reserved
  names, and no jobs or exports.
- Stage 4 adds nine deliberate forward migrations: two market options, five
  company, and two news resources. Their frozen identities, paths, and hashes
  are recorded in the [migration reconstruction map](docs/rebuild/MIGRATION_RECONSTRUCTION.md).
- The primary gate imports exactly 17 reviewed synthetic fixtures: seven
  company, five options, and five news. It passes 164 full-suite tests and 27
  focused Stage 4 tests, with deterministic two-root strict-JSON evidence.

### Exit gate

- Company ticker changes and joint filings preserve issuer/accession identity.
- A news as-of query excludes later local captures.
- Option analysis cannot combine capture IDs, feeds, environments, or
  nonstandard deliverables silently.
- All domain tests use temporary stores and redacted fixtures.

The primary fixture gate and independent SolUltra verification completed
these checks. `G4` is complete.

## 9. Stage 5 — Restore the composable tool platform

Status: **Implemented, fixture-validated, and independently verified.**

The contract is defined in
[TOOL_PLATFORM_SPEC.md](docs/rebuild/TOOL_PLATFORM_SPEC.md). Deterministic
receipts and the primary-gate result are recorded in the
[Stage 5 acceptance evidence](docs/rebuild/STAGE5_EVIDENCE.md).

### Deliverables

- Shared typed primitives for discovery, retrieval, transformation, alignment,
  statistics, econometrics, research contracts, and strict serialization.
- Public compatibility adapters for the reviewed 57 tool names.
- Registry-derived input/output schemas, examples, limits, and routing.
- Tool-version and data-lineage metadata in every research response.
- Additive compatibility and explicit deprecation rules.

### Fixture-validated scope

- Registry revision `2.3.0` and schema version `1.1.0` expose all 57 reviewed
  names through generated input and output schemas and closed routing.
- The two Stage 1 contracts retain their historical projection; the other 55
  are explicit forward reconstructions. Unsupported fixture semantics return
  `not_established`, and the live intraday capability is disabled.
- Direct and loopback HTTP calls share typed validation, strict JSON, bounded
  concurrency/deadlines/cancellation, deterministic receipts, and read-only
  four-store access.
- The primary gate passed 207 tests and a deterministic two-clean-root rebuild
  without adding migrations, jobs, exports, live providers, or UI code.

### Exit gate

- Every public schema rejects unknown properties.
- Caller input cannot choose database paths or submit SQL.
- Equivalent HTTP and in-process calls produce equivalent contracts.
- Every limit is enforced before expensive work begins.
- Results are deterministic, read-only, finite JSON with explicit exclusions
  and warnings.

The primary fixture gate and independent SolUltra verification completed these
checks. `G5` is complete. The bounded Stage 6 portal primary gate has since
passed; its unavailable browser-automation check was explicitly waived by the
user before the Stage 6 commit and Stage 7 authorization.

## 10. Stage 6 — Restore portal and research surfaces

Status: **Implemented and accepted: bounded offline four-route portal.** The
offline executable verifier passed; on 2026-08-10 the user explicitly accepted
the unavailable browser-automation check and authorized the Stage 6 commit.

The shared visual and interaction language is defined in
[UI_VISUAL_DIRECTION.md](docs/rebuild/UI_VISUAL_DIRECTION.md). The bounded
implementation establishes the first local application shell and registry
exposures. The broader chart/algorithm/backtest surface remains deferred.

### Implemented bounded scope

- One persistent local application shell using the accepted Inter-based,
  restrained institutional light token set.
- Four fixed local-private routes: Overview (`/`), GDP Vintages
  (`/gdp-vintages`), Tables (`/table-inspector`), and Agent Tools
  (`/agent-tools`).
- Frozen Stage 6 registry projection `2.4.0` and schema `1.2.0` dashboard exposures
  with fixed datasets, relations, filters, server-owned sort fields,
  pagination, tool dependencies, and API routes.
- Read-only, query-only services and loopback boundary checks, including
  source/restored store mutation fingerprints and fixed security headers.
- Locally bundled CSS, JavaScript, and pinned Inter 4.1 font/OFL license
  assets; no reference-site, remote-font, analytics, or tracker asset.
- No jobs, exports, live providers, runtime network work, scheduler
  installation, promotion, hosting, Atlas, or destructive storage operation.

### Formal analytical scope remains deferred

Charts, shared indicator research, algorithms, and walk-forward/backtest
reports are not implemented or claimed by this bounded portal. They require
their own causal-timing, cost, canonical-backend, golden-vector, and
acceptance evidence before any implementation decision. Atlas remains Stage 8
work and cannot be opened by this portal's primary gate.

### Accepted bounded exit

The bounded four-route portal exit is accepted. Offline independent checks
proved the read-only API, mutation neutrality, fixed registry contracts,
strict JSON handling, local assets, contrast, and deterministic two-root
evidence. The Codex browser controller could not attach even though the local
portal was visibly running; on 2026-08-10 the user explicitly waived that
automation check, authorized the Stage 6 commit, and opened bounded offline
Stage 7 work. The following broader analytical requirements remain deferred
and are not claimed by this Stage 6 commit:

- The browser has no ingestion or raw-SQL capability.
- All database connections are read-only and query-only.
- Warm-up and missing observations render as gaps rather than zero.
- Indicator calculations have golden vectors and one canonical backend.
- Backtests use training-only transformations, explicit decision/execution
  times, costs, benchmarks, and deterministic ties.
- Visual-regression, responsive, keyboard, reduced-motion, contrast, and
  locally bundled font checks pass without any reference-site runtime asset.

## 11. Stage 7 — Restore scheduling and operations

Status: **Implemented and independently verified for bounded offline manual
fixture rehearsal.** The accepted
evidence is recorded in
[Stage 7 acceptance evidence](docs/rebuild/STAGE7_EVIDENCE.md).

The contract is defined in
[SCHEDULING_AND_LOCKING.md](docs/rebuild/SCHEDULING_AND_LOCKING.md).

### Implemented bounded scope

- Canonical registry `2.5.0` / schema `1.3.0` declares exactly eight
  disabled `manual_fixture_only` jobs, derived write-store sets, bounded
  step graphs, and zero exports.
- Deterministic, path-free dry-run plans create no store, state, lock, provider,
  or scheduler side effect.
- All fixture candidates are parsed and sealed before acquisition of the
  complete physical-store lock session; publication reuses that session and
  does not nest locks.
- Retry classes, active step timeouts, dependency policies, aggregate sysexit
  precedence, and lock/I/O/configuration failures are bounded and receipt-safe.
- Private logs, optional monthly markers, and receipts are immutable and
  atomically published with the receipt last; a publication failure cannot
  report success.
- The restricted manual wrapper requires explicit initialized synthetic stores
  and an explicit private state root. It preserves aggregate exit codes.
- SQLite online backup/restore rehearsal proves integrity, foreign keys,
  read-only equality, and source mutation neutrality.

### Primary exit evidence

The primary gate passed 277 offline tests, including 43 operations tests and
three deterministic Stage 7 integration checks. It proves changed then
unchanged real fixture execution, exact replay no-write behavior, deterministic
multi-lock ordering/release, timeout and lock-contention handling, private
evidence safety, two-root equality, and nonempty-root refusal.

No live provider, runtime network, scheduler installation/start/update/removal,
export, promotion, hosting, Atlas, default path, or destructive operation is
authorized or implemented. Recovered external task definitions, calendars,
identity, and timezone remain unresolved; the eight jobs stay disabled. Stage
8 remains closed and requires separate authorization.

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
