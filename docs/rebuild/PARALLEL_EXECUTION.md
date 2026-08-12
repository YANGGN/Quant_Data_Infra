# Parallel Execution Plan

Status: Accepted  
Planning baseline: 2026-08-09  
Scope: dependency-aware multi-agent execution of the rebuild roadmap

## 1. Purpose

This document explains how to run useful parts of the rebuild concurrently
without bypassing the exit gates in [ROADMAP.md](../../ROADMAP.md) or allowing
multiple agents to redefine shared semantics. It complements the project agent
policy in [AGENTS.md](../../AGENTS.md).

Stages 1 through 5 are implemented and independently verified. The bounded
Stage 6 four-route local portal passed offline and independent verification;
the user explicitly waived its unavailable browser-automation check. The
bounded offline Stage 7 manual fixture rehearsal is implemented and
independently verified. The bounded fixture-only Stage 8 JSON snapshot and
static Atlas passed their primary and independent offline gates. Browser
verification remains pending.

The user has separately authorized one tightly bounded Stage 9 preparation:
a manual FMP daily OHLCV backfill for `SPY`, inclusive `2026-07-01` through
`2026-07-31`, in an exact non-production root. Its primary and independent
offline gates and bounded live receipt passed; there is no authorization for
promotion or old-store retirement.

## 2. Capacity and operating model

The project configuration assumes one SolUltra primary plus at most three
concurrent subagents:

- the primary owns decomposition, shared interfaces, integration, and gates;
- TerraMax implementation agents own disjoint bounded lanes;
- the SolUltra verifier independently tests and reviews an integrated wave; and
- completed agents free capacity for the next queued lane.

Parallelism is a throughput tool, not an objective by itself. Use all available
slots when work is genuinely independent. Queue or serialize work when two
lanes share a file, identifier space, schema decision, database, or acceptance
boundary.

## 3. Dependency graph

```text
G0  Accept planning package and close Stage-1 decisions
 |
B1  Freeze the Stage-1 shared spine
 |   registry subset, StoreMap, migration convention, time/identity types,
 |   read boundaries, strict JSON/error contracts, temporary-store harness
 |\
 | +-- Market vertical-slice lane ---------+
 | +-- Macro vertical-slice lane ----------+--> G1 Stage-1 exit gate
 | +-- Tool/dashboard/test-boundary lane --+
 |
G2  Four-store foundation (fixture-validated; unified runtime mode retired)
 |  Stage 3 separately authorized for bounded offline fixture restoration
 |-- in parallel: Market restoration
 |-- in parallel: Macro restoration
 v
G3  Stage-3 primary fixture gate and SolUltra verification passed
 |  Stage 4 bounded offline synthetic-fixture scope authorized
 |-- in parallel: Company restoration
 |-- in parallel: News restoration
 |-- in parallel: Options restoration (options shares the market store)
 v
G4  Stage-4 primary fixture gate and SolUltra verification passed
 |  Stage 5 primary fixture gate and independent verification passed
 |
T0  Freeze typed tool core, schema generator, and compatibility decision
 |\
 | +-- Domain adapter families --+
 | +-- Research/statistics tools -+--> G5 Tool-platform exit gate
 | +-- HTTP/contract parity -------+
 |
G6  Bounded four-route portal independently verified; browser automation waived
 |
G7  Manual-first scheduling and operational readiness
 |
G8  Bounded fixture JSON snapshot and dependency-free static Atlas
 |
G9  One manual FMP SPY non-production backfill; candidate-only acceptance pending
```

`G0` through `G9` are integration gates, not agent tasks. A later stage may
have read-only design, test planning, or fixture preparation in progress, but
its executable implementation cannot land before the preceding gate passes.

## 4. Hard serialized owners

The following areas define shared meaning and have exactly one writer at a
time:

| Area | Integration-owned artifact | Why it serializes |
| --- | --- | --- |
| Migration system | Store/ordinal/resource/checksum/reconstruction map, migration runner, control-plane DDL | Recovered semantics are not byte-exact DDL or checksums; ordinal collisions or false parity are irreversible errors |
| Registry | Canonical registry, JSON schema, validator, generated exposure artifacts | One source must own store, dataset, job, tool, and export identities |
| Temporal core | Precision types, cutoff comparator, version selector, tombstone and semantic replay rules | Domains must not reinterpret `latest`, `as_of`, `first_release`, or availability |
| Path and locks | Store resolver, alias detection, physical lock key, multi-lock helper | Writers, scheduler, backup, and export must use the same physical identity |
| Tool contracts | Typed core, schema generation, public-name inventory, route manifest | Adapters cannot maintain divergent schemas or embed parallel business logic |
| Fixtures and goldens | Shared fixture manifest, semantic digests, approved golden outputs | An implementation author cannot silently update expected evidence to pass |
| Generated artifacts | Registry-derived code/docs, package exports, root router | Concurrent regeneration creates stale or internally inconsistent output |
| Architecture | `AGENTS.md`, `.codex/`, ADRs, architecture-wide contracts | Agent policy and target semantics require one integration decision |

Domain agents submit scoped registry or migration fragments to the integration
owner. They do not directly merge canonical declarations concurrently.

## 5. Wave plan

### Wave 0 — Acceptance and implementation baseline

Parallel read-only lanes may review architecture, ADRs, focused contracts, the
57-name compatibility choice, and the vertical-slice open decisions. The
primary resolves conflicts and records user decisions.

Exit conditions:

- Stage 0 is accepted by the user;
- the two-tool vertical-slice registry is explicitly test-only or reconciled
  with the 57-name compatibility decision;
- unified runtime compatibility is retired and all four store paths must be
  physically distinct; and
- the user authorizes a fresh Git baseline if implementation is to start.

### Wave 1 — Offline vertical slice

The primary first freezes `B1`. Then three lanes can run concurrently:

| Lane | Owned outcome | Must not own |
| --- | --- | --- |
| Market slice | Daily-price evidence, canonical correction behavior, market fixtures and focused tests | Shared time semantics, canonical registry, macro paths |
| Macro slice | Revision-aware observations, releases/vintages, macro fixtures and focused tests | Shared cutoff comparator, canonical registry, market paths |
| Boundary/harness | Four temporary-store isolation, two read-only tools, minimal inspection route/page, strict JSON and mutation tests | Domain fact semantics or shared migration allocation |

The primary integrates the shared registry, paths, migrations, temporal types,
and read contracts. The verifier then runs the complete Stage-1 acceptance
suite, including clean-rebuild equivalence and future-evidence exclusion.

### Wave 2 — Four-store foundation

Status: **Implemented offline and fixture-validated.** Its evidence is
recorded in [Stage 2 acceptance evidence](STAGE2_EVIDENCE.md). The completed
scope comprises company/news empty-domain initialization; ten store-local
migrations; registry reconciliation; shared artifact/snapshot/quality/run-audit
control-plane conventions; strict routing and physical locks; read-only health;
cross-store composition receipts; and WAL-safe backup/restore mechanics.

The primary owned registry reconciliation, the migration map, shared
control-plane conventions, and enforcement of the retired unified runtime mode.
This foundation did not itself authorize market or macro restoration, live
providers, scheduler jobs, exports, Atlas, promotion, or destructive
operations. Stage 3 required its own bounded authorization.

### Wave 3 — Market and macro restoration

Status: **Implemented — fixture-validated and independently verified.**
Evidence is recorded in
[Stage 3 acceptance evidence](STAGE3_EVIDENCE.md). The completed bounded scope
has three market and eight macro forward migrations, 25 new reviewed synthetic
fixtures, and explicit no-write, point-in-time, tombstone/restoration,
backup/restore, and two-root evidence checks. It adds no live provider or
network access, jobs, exports, public tools, company/news/options restoration,
or CUSIP-level SOMA storage.

Market and macro may run in parallel because they own distinct operational
stores. Each lane remains internally ordered as specified in
[the roadmap](../../ROADMAP.md): identity precedes facts, and facts precede
survivorship-sensitive or vintage-sensitive research.

A third lane may build independent property, recovery, and cross-store cutoff
tests. It must not change shared temporal semantics or approve new goldens.
Independent verification inspected the integrated workspace and reproduced the
gate. That pass does not itself authorize Stage 4.

### Wave 4 — Company, news, and options restoration

Status: **Implemented, fixture-validated, and independently verified.**
Evidence is recorded in [Stage 4 acceptance evidence](STAGE4_EVIDENCE.md). The integrated bounded scope
has nine forward migrations and 17 reviewed synthetic fixtures: seven company,
five options, and five news. It remains offline-only and adds no public tools,
jobs, exports, promotion, dashboard, hosting, or destructive operation.

Company, news, and options are three domain lanes. Options is a market-store
domain, so its migration window and physical writes must not overlap market
migration work. The primary owns any multi-store context used by options and
macro together. The SolUltra verifier independently inspected the integrated
workspace and completed `G4`.

### Wave 5 — Tool platform

Status: **Implemented, fixture-validated, and independently verified.**

The primary serially freezes `T0`: typed contracts, dispatcher, schema
generation, resource bounds, strict serialization, and the accept-or-retire
decision for the 57 public names.

After `T0`, adapter families can run concurrently by domain. Adapters may call
shared primitives and registered gateways; they may not embed SQL, point-in-time
selection, formulas, or hand-maintained alternate schemas.

The integrated primary gate covers all 57 generated contracts, direct/HTTP
parity, strict bounded receipts, typed composability, honest
`not_established` results, and total store-mutation neutrality. Evidence is
recorded in [Stage 5 acceptance evidence](STAGE5_EVIDENCE.md). The independent
verifier closed `G5`. The bounded Stage 6 portal primary gate has since passed;
its offline independent checks passed and the unavailable browser-automation
check was explicitly waived by the user.

### Wave 6 — Portal and research surfaces

Status: **Bounded four-route local portal accepted; browser automation
explicitly waived by the user.**

The Stage 6 primary integration owner froze the historical registry
`2.4.0`/`1.2.0` dashboard projection before writers worked. The completed
bounded portal has four local-private routes: Overview (`/`), GDP Vintages
(`/gdp-vintages`), Tables (`/table-inspector`), and Agent Tools
(`/agent-tools`). Its loopback services use the existing explicit, read-only
four-store boundary; they do not create dashboard-specific data semantics.

The primary gate covers the shared shell, local pinned assets, registry-derived
navigation and allowlists, bounded GDP and table read services, the 57-tool
Agent Tools surface, source/restored no-write fingerprints, deterministic
two-root evidence, and zero runtime network assets. Independent verification
re-derived and tested those claims. The browser-control runtime could not
attach; the user explicitly accepted that external limitation.

Charts and shared indicator presentation, algorithms, and walk-forward reports
are not part of this bounded implementation. They remain closed until their
causal timing, cost, canonical-backend, golden-vector, and formal acceptance
requirements are explicitly authorized and met. This portal primary gate does
not itself define scheduling semantics. The user separately authorized bounded
offline Stage 7 implementation. The user later authorized the bounded
fixture-only Stage 8 JSON snapshot and static Atlas. Its correction and formal
verification remain separate from Stage 6; this adds no Parquet, DuckDB,
package, hosting, deployment, live-provider, scheduler, default-path, or
destructive scope.

### Wave 7 — Scheduling and operations

Status: **Bounded offline manual fixture rehearsal implemented and independently
verified.**

The integration owner serialized canonical registry `2.5.0`/`1.3.0`,
physical lock capabilities, receipt contracts, fixture bindings, golden
evidence, and root CLI work. Disjoint lanes implemented importer
prepare/publish seams, the fixture executor, runner/retry/receipt primitives,
and focused failure tests.

All eight jobs remain disabled `manual_fixture_only` declarations. Candidates
are prepared before the complete ordered lock session; publication reuses that
session. The gate covers changed and unchanged replay, lock contention and
release, active timeout, dependency and exit-code precedence, immutable
receipt-last evidence, backup/restore, and a restricted explicit-root manual
wrapper. Evidence is recorded in
[Stage 7 acceptance evidence](STAGE7_EVIDENCE.md).

No live provider, runtime network, scheduler installation/start/update/removal,
export, promotion, hosting, Atlas, default path, or destructive action is part
of Wave 7. The separately authorized Stage 8 scope does not alter that
historical Wave 7 boundary.

### Wave 8 — Analytical exports and Atlas

Status: **Bounded offline implementation complete and independently verified;
browser verification pending.**

Wave 8 is deliberately limited to one fixture-only, manual strict-JSON snapshot
over the four registered market, macro, company, and news projections, plus a
dependency-free static Atlas consumer. It is a forward reconstruction, not
recovered historical Atlas or 13-dataset parity.

The integration owner serializes registry/schema reconciliation, the complete
physical-lock and SQLite online-backup copy protocol, the exact host-selected
derived-output root, immutable revision publication, private receipt, and
atomic `current` pointer. The static lane reads only relative public
manifest/chunk assets; it has no API or operational-store connection.

Parquet, DuckDB, package installation, hosting, deployment, live providers,
scheduler operations, default paths, operational-store promotion, and
destructive cleanup are outside Wave 8. All writers stop before independent
verification. The completed primary correction/reseal and independent review do
not substitute for browser verification. Current status belongs in
[Stage 8 evidence](STAGE8_EVIDENCE.md).

### Wave 9 — Bounded FMP non-production backfill

Status: **Bounded live population independently verified.** The only live
lane is `fmp.market.daily_price_backfill` for `SPY`, inclusive `2026-07-01`
through `2026-07-31`, under the exact non-production root
`/home/volatility/quant-data-nonprod/stage9-fmp-spy-202607`. It is one request,
no retry, manual-only, and candidate-only; it is not a general parallel
provider program.

The primary serializes the registry/migration boundary, exact target preflight,
single FMP request, reconciliation, replay, backup/restore, scratch-only
correction, and candidate receipt. No worker adds a scheduler, public
tool/dashboard/Atlas exposure, promotion, default path, or retirement action.

The independent verifier passed the Stage 9 offline checks after corrections
and separately verified the bounded live receipt. Provider-use rights remain
the operator's account-specific responsibility.

## 6. Four-thread cadence

Use the available threads in two modes:

| Phase | Primary | Subagent 1 | Subagent 2 | Subagent 3 |
| --- | --- | --- | --- | --- |
| Build | Freeze interfaces and integrate shared files | TerraMax writer, lane A | TerraMax writer, lane B | TerraMax writer, lane C |
| Gate | Reconcile evidence; no active writers | SolUltra verifier | Optional second read-only specialist | Idle or read-only diagnostics |
| Correct | Integrate or assign the fix | One TerraMax correction owner | Read-only impact analysis | Idle |
| Re-gate | Record results; no active writers | SolUltra verifier | Optional read-only regression review | Idle |

Do not let a fixer modify the workspace while a verifier is still establishing
its baseline. Verification reports return to the primary; verifiers do not
silently patch findings.

## 7. Integration checkpoints

Every wave records:

1. accepted inputs and fixed interfaces;
2. agent ownership map and changed paths;
3. registry and migration reconciliation;
4. focused test evidence from each lane;
5. integrated offline acceptance results;
6. independent verifier findings and disposition;
7. clean rebuild, replay, read-only, and recovery evidence where applicable;
   and
8. the explicit decision to open or keep closed the next gate.

Passing isolated lane tests is not sufficient. The stage exit gate must pass as
one integrated system.

## 8. Stop conditions

Stop the affected parallel lane when:

- a Proposed contract requires an unrecorded semantic choice;
- two agents need the same path, registry ID, migration ordinal, or physical
  store write window;
- migration bytes/checksum/reconstruction status is unknown;
- a domain needs to redefine shared identity, availability, missingness, or
  tombstone behavior;
- a fixture or golden output must change alongside its implementation without
  independent review;
- an offline test would touch network, credentials, a default database, or a
  live path;
- an options or other multi-store operation discovers a required store after
  execution begins;
- a public read path initializes, migrates, or writes a store;
- an export lacks a consistent source copy, exact staging containment, or
  honest per-store receipts; or
- a later-stage implementation would cross an unmet exit gate.

## 9. Worktrees and temporary roots

Before a Git baseline exists, parallel writers share one workspace and must use
strict disjoint path ownership. After the user authorizes and creates a fresh
baseline, prefer one worktree per write-capable agent when supported. Worktrees
reduce filesystem conflicts but do not relax semantic ownership: migrations,
registry IDs, shared contracts, and generated artifacts still serialize.

Every agent and test process uses an explicit unique temporary root. Tests must
never infer or fall back to operational store paths.
