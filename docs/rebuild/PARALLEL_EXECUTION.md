# Parallel Execution Plan

Status: Accepted  
Planning baseline: 2026-08-09  
Scope: task-sized coordination and historical rebuild-wave reference
Revised: 2026-09-05 — user-approved lightweight delegation and role-neutral routing

> **Read the applicable coordination section only.** Bounded investigation and
> disjoint implementation use Section 2 with the [fast path](FAST_PATH_DEVELOPMENT.md).
> Delegation alone does not require a formal wave or independent certification.
> The historical wave plans apply only when an accepted gate calls for them.

Sections 1, 3, and 5 summarize completed rebuild scopes as recorded in their
linked evidence. Their exclusions do not grant new authority or cancel later
explicit decisions. The [operating envelope](CURRENT_OPERATING_ENVELOPE.md)
is the sole index of recorded operational authorization and dated activation
evidence; it is not a live host-health report.

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
static Atlas passed their primary and independent offline gates. On 2026-08-14
the user explicitly waived unavailable in-app browser automation and accepted
the bounded Stage 8 gate.

The user has separately authorized one tightly bounded Stage 9 preparation:
a manual FMP daily OHLCV backfill for `SPY`, inclusive `2026-07-01` through
`2026-07-31`, in an exact non-production root. Its primary and independent
offline gates and bounded live receipt passed; there is no authorization for
promotion or old-store retirement.

The retained Stage 10 base and historical-extension candidates are complete
and independently re-inspected; no provider request may be repeated. Stage 11
is complete as a private candidate in the same isolated cohort: BEA, EIA
retail, and EIA weekly are published, targeted checks passed, and the private
receipt is retained. No Stage 11 request may be repeated.

The bounded offline Stage 12A authority/path gate is implemented and independently
verified; see [Stage 12 evidence](STAGE12_EVIDENCE.md). Stage 12B is implemented
and independently verified offline and fixture-only under its
[focused collector contract](STAGE12B_INCREMENTAL_MARKET_V1.md) and
[evidence record](STAGE12B_EVIDENCE.md). Its gate did not authorize later work.
**Stage 12C is complete and independently verified** under
[the focused two-session contract](STAGE12C_MARKET_GAP_V1.md) and
[evidence record](STAGE12C_EVIDENCE.md). Stage 12D is complete and
independently verified under its
[no-transfer adoption/freeze contract](STAGE12D_PROJECT_LOCAL_OPERATIONALIZATION.md)
and [evidence record](STAGE12D_EVIDENCE.md). That proof granted no Stage 12E
authority; subsequent decisions are indexed in the operating envelope.

## 2. Capacity and operating model

The [routing guide](../../.codex/README.md) and its linked TOML files own current
role/model/effort choices, including UI design. The September 5 user-approved
routing replaces the former fixed implementation/verifier pair for future work;
historical wave evidence and named completed checks remain unchanged.
Check effective routes; configuration edits need a fresh trusted-project session.

Use one integration owner and one writer by default, normally zero or one
subagent, and no more than three concurrent subagents within configured runtime
capacity. Role presets do not imply that every role runs on every task.

- The integration owner controls shared semantics, critical implementation,
  final integration, and completion; it directly finishes small tasks.
- Bounded and substantial implementation roles own disjoint domain-local paths
  after shared interfaces are settled. Mechanical work needs an exact,
  checkable transformation; extra effort grants no broader responsibility.
- Read-only investigators return evidence for a defined question.
- Read-only analysts challenge designs and trace concrete failure paths.
  Adversarial test authors own only assigned tests and temporary-fixture helpers.
- A fresh independent verifier assesses a stable integrated baseline without
  fixing it or having authored its implementation or tests.
- The configured UI design owner handles every UI/UX and visual decision,
  implementation requiring design judgment, and visual review. Other workers
  may execute a settled mechanical UI specification. Browser checks still apply.

Delegate only when a bounded question or independent lane saves time or improves
quality. A single command or search rarely needs an agent. Use task-scoped lanes
rather than permanent domain teams. Queue work sharing files, identifiers,
schema decisions, stores, or acceptance boundaries. Do not require a verifier
solely because a worker helped, or automatically run both analysis and review.
Mandatory independent gates still apply; advisory analysis is not certification.

### Delegated task contract

Keep the assignment compact and complete:

1. Objective, intended behavior, and required output.
2. Read-only scope, or exact owned write paths and prohibited shared edits.
3. Current authority, fixed interfaces, relevant invariants, acceptance cases,
   and required checks.
4. Known risks and the boundary that returns a decision to the integration owner.

Provide relevant references or a compact context package. Do not routinely
copy the entire conversation or rebuild history into each agent.
Code searches stay within relevant source/docs/test directories, not operational
data roots. Investigators do not open stores, run live diagnostics, or obtain
credentials merely to answer source questions.

Parallel writers require disjoint ownership. Executors do not recursively
delegate. A needed change outside ownership returns to the primary. Known
shared-safety risk determines stronger ownership before implementation; an
unresolved ambiguity or failed speculative fix returns with the failure and
attempted approach rather than another speculative repair cycle.

Each actionable analysis/review finding identifies the trigger, violated
requirement/invariant, evidence, and smallest sufficient correction. Optional
improvements stay separate; added infrastructure requires an accepted need.
Preserve focused and mandatory validation gates. Batch concrete corrections,
then repeat only affected checks and any newly required adjacent/exit checks.

Return the answer or changed paths, actual commands/results, findings,
limitations, and integration-owner actions. A formal verifier also states whether
the required evidence passed. Authored tests and self-review do not establish
independent verification. Do not duplicate unchanged plans or claim checks
that were not performed.

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
G8  Bounded fixture JSON snapshot and static Atlas; accepted with browser waiver
 |
G9  One manual FMP SPY non-production backfill; candidate receipt verified
 |
G10 Frozen 629-symbol market-history base and extension; receipts retained
 |
G11 Bounded BEA/EIA macro candidate complete; no repeat or promotion
 |
G12A Market v1 authority, explicit roster, and canonical-path offline gate independently verified
 |
G12B Fixture-only incremental collector independently verified; no live/provider/store cutover/scheduler work
 |
G12C Bounded no-copy two-session population complete; private receipt retained
 |
G12D No-transfer adoption/freeze complete; later authority indexed in operating envelope
```

`G0` through `G12D` are integration gates, not agent tasks. `G12B` remains
within its immutable fixture-only boundary; `G12D` passed within its fixed
no-transfer contract and immutable [evidence record](STAGE12D_EVIDENCE.md).

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
accepted with an explicit browser-automation waiver on 2026-08-14.**

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
verification. The completed primary correction/reseal and independent review
remain the executable evidence; the explicit user waiver closes the bounded
gate without claiming unavailable browser checks passed. Current status belongs
in [Stage 8 evidence](STAGE8_EVIDENCE.md).

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

### Wave 10 — Bounded FMP market history

Status: **Retained base and eight-window extension candidates complete and
independently re-inspected.**

The lane is frozen to 629 symbols in the exact isolated Stage 10 target. The
base and all 5,032 extension windows have immutable receipts. No writer may
repeat a provider request. Promotion, retirement, scheduling, public exposure,
tools, dashboards, Atlas, exports, and destructive operations remain outside
the wave. Evidence is recorded in
[Stage 10 evidence](STAGE10_EVIDENCE.md).

### Wave 11 — Bounded BEA/EIA macro history

Status: **Private, non-production candidate complete.**

The final serialized resume skipped completed BEA and retail work and completed
one weekly `PET.WCESTUS1.W` request. Targeted integrity, duplicate, and
current-pointer checks passed and the private candidate receipt is retained.
The wave authorizes no additional request, promotion, scheduling, or public
exposure. Evidence is recorded in
[Stage 11 evidence](STAGE11_EVIDENCE.md).

### Wave 12A — Market v1 authority and canonical path

Status: **Implemented and independently verified.**

The primary owns the path decision, registry revision `2.12.0`, historical
projection compatibility, and integration. One isolated implementation lane
owns the strict Market v1 manifest/loader/gate/tests. It freezes the explicit
629-symbol retained roster and binds the source-only Stage 10 resume/scope
digests without opening the retained SQLite stores.

No lane called a provider, consulted credentials, created/opened a default
database, copied or moved data, changed a scheduler, reopened Stage 10/11, or
began Stages 12B through 12E. The independent SolUltra verifier checked
deterministic evidence, hostile manifest mutations, path neutrality, historical
registry hashes, the focused suite, and the complete dependency-free suite.
Evidence is recorded in [the Stage 12 contract](STAGE12_MARKET_V1.md) and the
[Stage 12 evidence record](STAGE12_EVIDENCE.md).

### Wave 12B — fixture-only incremental Market v1 collector

Status: **Implemented and independently verified — offline fixture-only.**

The primary owns registry `2.13.0`/schema `1.8.0`, the exact `2.13.0` to
`2.12.0` historical projection, shared contracts, and integration. Scoped
implementation lanes may build only the collector described by the
[Stage 12B contract](STAGE12B_INCREMENTAL_MARKET_V1.md): the frozen 629-symbol
scope, injected response fixtures, and existing `0010` capture/version/current
model in explicit temporary fixture stores. No lane may use a live provider,
API key, environment configuration, network, default or retained store, new
migration, promotion, project-local cutover, public consumer, or scheduler.

The completed fixture gate, correction history, deterministic two-root
evidence, focused compatibility checks, and full dependency-free suite are
recorded in [Stage 12B evidence](STAGE12B_EVIDENCE.md). Completion does not
broaden the boundary above.
Stage 12A/B evidence remains immutable and retains its own offline boundary.

### Wave 12C — bounded two-session Market v1 gap completion

Status: **Implemented and independently verified — bounded manual no-copy
completion.** The primary owns registry `2.14.0`/schema `1.8.0`, its exact
`2.13.0` projection, and integration. The completed lane was limited to the
[Stage 12C contract](STAGE12C_MARKET_GAP_V1.md) and its immutable
[evidence record](STAGE12C_EVIDENCE.md): 629 one-attempt units closed as 619
published complete, three successful-empty, and seven narrowly sealed
authorized HTTP 402 outcomes. The exact private receipt, final counts, and
`mode=ro&immutable=1` source-neutrality method are recorded there. The lane
does not authorize a repeat, transfer, public consumer, or scheduler.

### Wave 12D — no-transfer project-local adoption/freeze

Status: **Complete and independently verified — no-transfer read-only
adoption/freeze.**

The primary owns the accepted [Stage 12D contract](STAGE12D_PROJECT_LOCAL_OPERATIONALIZATION.md),
[immutable evidence](STAGE12D_EVIDENCE.md),
[ADR 0010](../adr/0010-stage12d-no-transfer-market-adoption.md), fixed scope,
and integration. The bounded and independent gates passed, exactly two
canonical proofs produced immutable receipts with one semantic proof, and the
target plus sidecar stamps remained unchanged. The independent reconciliation
did not reopen SQLite or compute a new full-database hash. No copy, move,
replacement, transfer, backup, migration, registry change, provider/network
access, scheduler change, or consumer exposure occurred within that proof.
Later Stage 12E decisions are indexed in the operating envelope; the Stage 12D
contract and evidence retain their original scope.

## 6. Four-thread cadence

Use this cadence only for work requiring formal independent verification.
Bounded investigation and ordinary implementation need no wave ceremony.
Stop writers before a verifier establishes its baseline; otherwise explicitly
bound review to an immutable revision or snapshot without claiming to verify
ongoing edits.

| Phase | Primary | Subagent 1 | Subagent 2 | Subagent 3 |
| --- | --- | --- | --- | --- |
| Build | Freeze interfaces and integrate shared files | Implementation worker, lane A | Implementation worker, lane B | Implementation worker, lane C |
| Gate | Reconcile evidence; no active writers | Independent verifier | Optional second read-only specialist | Idle or read-only diagnostics |
| Correct | Integrate or assign the fix | One correction owner | Read-only impact analysis | Idle |
| Re-gate | Record results; no active writers | Independent verifier | Optional read-only regression review | Idle |

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

Before substantial edits, inspect the branch, index, uncommitted work, and
required prerequisites. Identify dependencies on unrelated changes early;
preserve them and obtain scope authority before including them in a task commit.
Commit and push only when requested or already authorized for this scope.

Use separate worktrees when they help independent writers, with an explicit
known starting state. A worktree does not automatically contain uncommitted
prerequisites. Worktrees do not relax semantic ownership: migrations, registry
IDs, shared contracts, and generated artifacts still have one integration owner.
Do not search for lost history or imply a recovered baseline.

Every agent and test process uses an explicit unique temporary root. Tests must
never infer or fall back to operational store paths.
