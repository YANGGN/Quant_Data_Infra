# Parallel Execution Plan

Status: Accepted  
Planning baseline: 2026-08-09  
Scope: dependency-aware multi-agent execution of the rebuild roadmap

## 1. Purpose

This document explains how to run useful parts of the rebuild concurrently
without bypassing the exit gates in [ROADMAP.md](../../ROADMAP.md) or allowing
multiple agents to redefine shared semantics. It complements the project agent
policy in [AGENTS.md](../../AGENTS.md).

Stage 0 and the bounded offline Stage 1 slice were authorized on 2026-08-09.
This plan does not authorize executable work beyond the current stage gate.

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
G2  Four-store foundation (unified runtime mode retired)
 |-- in parallel: Market restoration
 |-- in parallel: Macro restoration
 v
G3  Stage-3 exit gate
 |-- in parallel: Company restoration
 |-- in parallel: News restoration
 |-- in parallel: Options restoration (options shares the market store)
 v
G4  Stage-4 exit gate
 |
T0  Freeze typed tool core, schema generator, and compatibility decision
 |\
 | +-- Domain adapter families --+
 | +-- Research/statistics tools -+--> G5 Tool-platform exit gate
 | +-- HTTP/contract parity -------+
 |
G6  Portal and research surfaces
 |
G7  Manual-first scheduling and operational readiness
 |
G8  Benchmarked exports and atomic Atlas publication
 |
G9  Explicitly approved controlled repopulation and promotion
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

After the Stage-1 gate, safe lanes are:

- company-store initialization and temporary fixtures;
- news-store initialization and temporary fixtures; and
- migration, health, backup, read-only-factory, and cross-store test mechanics.

The primary owns registry reconciliation, the migration map, shared
control-plane conventions, and enforcement of the retired unified runtime mode.

### Wave 3 — Market and macro restoration

Market and macro may run in parallel because they own distinct operational
stores. Each lane remains internally ordered as specified in
[the roadmap](../../ROADMAP.md): identity precedes facts, and facts precede
survivorship-sensitive or vintage-sensitive research.

A third lane may build independent property, recovery, and cross-store cutoff
tests. It must not change shared temporal semantics or approve new goldens.

### Wave 4 — Company, news, and options restoration

Executable Wave 4 work starts only after the Stage-3 `G3` exit gate passes.
Company, news, and options are then three parallel domain lanes. Options is a
market-store domain, so its migration window and physical writes must not
overlap market migration work. The primary owns any multi-store context used by
options and macro together.

### Wave 5 — Tool platform

The primary serially freezes `T0`: typed contracts, dispatcher, schema
generation, resource bounds, strict serialization, and the accept-or-retire
decision for the 57 public names.

After `T0`, adapter families can run concurrently by domain. Adapters may call
shared primitives and registered gateways; they may not embed SQL, point-in-time
selection, formulas, or hand-maintained alternate schemas.

### Wave 6 — Portal and research surfaces

After stable read contracts and shared navigation exist, parallel lanes can own:

- Overview, GDP Vintages, Tables, and Agent Tools;
- charts and shared indicator presentation; and
- algorithms and walk-forward reports, only after causal timing and cost
  contracts pass their gate.

The portal cannot define new data semantics or open writable database
connections.

### Wave 7 — Scheduling and operations

The physical-path lock primitive is designed and tested earlier as shared
infrastructure. Actual jobs remain manual-first and Stage 7.

After the scheduler core freezes, domain job wrappers may run in parallel.
Receipt schemas, lock identity, multi-lock ordering, retries, aggregate status,
backup/restore, and scheduler installation remain integration-owned. The
cross-store `options-close` job must acquire both macro and market locks before
publication.

### Wave 8 — Analytical exports and Atlas

Benchmark harnesses, analytical projections, Atlas projection/UI work, and
failure-injection tests may be separate lanes after the export manifest and
consistent-copy protocol freeze. Promotion remains one serialized operation:
validated staging followed by atomic pointer or directory replacement.

### Wave 9 — Controlled repopulation

After explicit approval, bounded backfills may run concurrently only when they
target distinct physical stores and independent provider limits. At most one
writer operates on a physical SQLite file. Reconciliation, backup, integrity,
point-in-time audit, and promotion remain serialized gates.

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
