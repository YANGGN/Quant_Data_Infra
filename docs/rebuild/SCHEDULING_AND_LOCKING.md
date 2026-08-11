# Scheduling and Physical-Store Locking Specification

Status: Accepted

## Purpose

This specification defines a manual-first path to safe scheduled collection.
Its bounded Stage 7 fixture rehearsal is implemented, but it deliberately
contains no task-installation or job-start commands. Recovered cadence
information remains evidence for parity, not evidence that a scheduler is
installed or authorized to contact a live provider or operational store.

Related documents:

- [Architecture](../../ARCHITECTURE.md)
- [System registry specification](SYSTEM_REGISTRY_SPEC.md)
- [Data and time contracts](DATA_AND_TIME_CONTRACTS.md)
- [Roadmap](../../ROADMAP.md)
- [ADR 0006: Lock by resolved physical store](../adr/0006-lock-by-physical-store.md)

## Scope

This document specifies:

- the proposed recovered job catalog;
- progression from static plans to optional external scheduling;
- logical job-to-store declarations and host path resolution;
- path-derived lock identity for the four distinct store paths;
- deterministic multi-lock acquisition and release;
- transaction, retry, failure, and replay rules;
- run receipts, dry-run output, exit codes, and private logging; and
- offline acceptance tests.

It authorizes only the reviewed synthetic-fixture rehearsal against explicit
temporary stores and private state roots. It does not authorize live/network
collection, operational or default database paths, scheduler installation,
update, removal or start, deployment, exports, promotion, or live diagnostics.

## Safety principles

1. Manual execution is the default. Scheduling is the last stage, not the
   mechanism used to discover whether a collector is safe.
2. A lock represents a resolved physical SQLite path, not a job name or merely
   a logical domain.
3. Every job declares all stores it may write before it starts.
4. Network fetch, retry, parsing, and bounded candidate staging finish before
   physical write locks are acquired.
5. The complete lock set is acquired before the first mutating publication
   step; lock upgrades are prohibited.
6. Network I/O never occurs inside a SQLite transaction or while a physical
   write lock is held.
7. Database write transactions are short, explicit, and idempotent.
8. Unchanged semantic content produces no ingestion-run, artifact, snapshot,
   membership, or fact write.
9. Partial, empty due to error, timed-out, or rate-limited responses are never
   evidence of deletion or unchanged content.
10. The aggregate process fails when any required or attempted independent step
   fails, even if later independent work succeeds.
11. Logs and receipts are private, bounded, and credential-free.

## Frozen disabled job catalog

Canonical registry `2.5.0` freezes the recovered names below as eight disabled
`manual_fixture_only` jobs. Store declarations are derived from their steps
for lock planning; they are not enabled schedules.

| Internal job | Recovered cadence target | Logical stores that may be written | Notes |
| --- | --- | --- | --- |
| `news-hourly` | Hourly at minute 10 | news | Bounded policy collection; disabled sources remain disabled by their separate policy. |
| `sec-daily` | Daily at 07:15 | company | SEC ticker/submission and CompanyFacts checks. |
| `options-close` | Weekdays at 13:20 and 16:20, calendar gated | macro, market | Recovered bundle refreshed Treasury evidence and then captured an option surface. If those steps are separated, the registry and lock set MUST change together. |
| `macro-daily` | Daily at 18:00 | macro | Calendar, funding, and release-aware official-source checks. |
| `market-close` | Daily or weekdays at 18:00 | market | Current equity, ETF, and index prices. |
| `expectations` | Weekdays at 20:00 | company | Immutable consensus and earnings-event snapshots. |
| `company-weekly` | Saturday at 09:00 | company | Bounded company-action capture. |
| `macro-monthly` | Sunday at 11:00 | macro | Slow-source freshness checks with a monthly success marker. |

Exact external task definitions, calendars, user identity, timezone, and
cadences MUST be reconciled against recovered evidence before any installation
proposal is approved.

## Manual-first progression

Progression is monotonic. Failure at a stage returns the job to that stage; it
does not authorize skipping ahead.

### Stage 0: static registry validation

Validate job names, step graphs, logical store declarations, dependencies,
timeouts, retry classes, calendars, required configuration names, and proposed
cadences. No provider, database, lock, log, receipt, or scheduler state is
touched.

### Stage 1: pure dry run

A pure dry run resolves configuration and planned physical-store identities,
then prints or returns a deterministic sanitized plan. It MUST NOT:

- call a provider;
- open or initialize a database;
- acquire a lock;
- create a scheduler task;
- create a monthly success marker; or
- execute a collector.

The plan includes job and step versions, dependency edges, logical stores,
deduplicated lock aliases, timeouts, retry classes, and expected exit behavior.
It MUST NOT include credentials or raw physical paths.

### Stage 2: offline execution rehearsal

This bounded stage is implemented with injected outcomes, reviewed synthetic
fixtures, temporary private state, and explicitly redirected temporary
databases. It exercises unchanged, changed, partial, retry, timeout,
configuration, receipt-I/O, and lock-contention paths and fails rather than
falling back to a default database. This evidence does not authorize Stage 3
live-provider work.

### Stage 3: explicitly authorized manual non-production run

Only an explicit user decision may authorize a real provider call or database
write. The target must be a new non-production store set. Review receipts,
idempotence, point-in-time behavior, failure isolation, lock behavior, and
backup/restore evidence before repeating the run.

### Stage 4: explicitly authorized operational manual run

An operational manual run requires exact store targets, backup and rollback
evidence, a reviewed plan digest, bounded scope, and an operator present. A
successful run does not install a schedule.

### Stage 5: external scheduling proposal

Only after repeated manual evidence may an installation change be reviewed.
Installation, update, removal, and job start remain separate explicit actions.
The external scheduler MUST use the same versioned job registry, wrapper, path
resolver, lock protocol, exit codes, and receipts proven manually.

## Job registry contract

Each job entry in the system registry MUST declare:

- stable job ID and semantic version;
- lifecycle state and whether scheduling is enabled;
- ordered step IDs and dependency edges;
- logical stores read and logical stores potentially written by every step;
- aggregate logical write-store set;
- provider/network capability per step;
- required configuration names without values;
- calendar and timezone policy;
- timeout and retry class per step;
- whether unchanged/no-write detection is supported;
- idempotency key or semantic identity version;
- monthly or other success-marker semantics;
- safe dry-run projection;
- receipt schema version;
- exit-code mapping; and
- owner and escalation rules.

The orchestrator MUST reject a step whose declared write set is not a subset of
the job's predeclared aggregate write set.

## Resolving logical stores to physical paths

The host resolves market, macro, company, and news paths from configuration.
The job cannot supply or override them.

For each declared logical write store, the resolver MUST:

1. obtain the host-selected configured path;
2. make it absolute under the operating environment's path rules;
3. normalize `.` and `..` without broad traversal;
4. resolve symlinks for an existing target;
5. for a not-yet-created target, resolve the nearest existing parent and append
   the remaining normalized components;
6. normalize case only when the underlying filesystem is case-insensitive;
7. reject an empty path, filesystem root, home directory, repository root, or
   directory where a database file is required; and
8. compare all declared paths for aliases before deriving locks.

Existing paths that are the same file according to the operating system MUST
collapse to one physical identity even when configured with different textual
paths. Hard-linked SQLite database aliases are unsupported because a stable
path lock cannot protect undisclosed aliases; if detected among configured
paths, startup MUST fail closed.

The resolver MUST NOT create a database, directory, WAL sidecar, migration
registry, or lock merely to determine identity.

## Path-derived lock identity

The physical lock identity is:

`SHA-256("quant-data-sqlite-lock-v1" + NUL + canonical_path_uri)`

The full lowercase digest is used as the lock key. The lock filename MAY use a
fixed prefix plus the digest. Human-readable job or domain names MUST NOT be
the synchronization key because:

- textual or symlink aliases may resolve to the same file; and
- one job may write more than one physical store.

Logs and public dry-run output SHOULD use a bounded alias such as `store-1`,
plus a short digest prefix if necessary for correlation. They MUST NOT reveal
the canonical path.

All cooperating writers, maintenance tasks, backup coordination, and exports
that require non-overlap MUST use the same identity algorithm and lock
directory. The algorithm version is part of the operational contract.

## Store layout

### Four split stores

Distinct market, macro, company, and news paths produce distinct lock
identities. Jobs on different files MAY proceed concurrently when neither job
declares the other's store.

### Duplicate physical paths

Unified runtime compatibility is retired. If two store roles resolve to the
same physical file, configuration validation fails before scheduling or lock
acquisition. The physical identity algorithm remains defense in depth and the
diagnostic basis for reporting the collision without exposing the path.

### Partial aliasing

Partial aliasing, such as market and macro sharing while company and news are
separate, MUST be rejected unless a future architecture decision explicitly
supports and tests it. A lock algorithm alone does not make an unsupported
schema layout valid.

## Deterministic multi-lock protocol

A job that may write several physical stores MUST:

1. resolve the complete set before any step starts;
2. deduplicate identical physical identities;
3. sort the canonical physical path URIs in ascending byte/lexical order;
4. acquire their corresponding digested lock keys in that order under one
   overall monotonic deadline;
5. start no mutating publication step until the full set is held;
6. perform no lock upgrade or out-of-order reacquisition; and
7. release all locks in reverse order in a `finally` path.

Using one overall deadline prevents an N-lock job from waiting the full timeout
N times. If any acquisition fails, the process MUST release every lock already
held, write a failed or blocked receipt when authorized state exists, and exit
with the lock-timeout status. It MUST NOT run a subset of the job.

Lock acquisition uses an operating-system advisory lock, not the existence or
contents of a file as proof of ownership. A lock file MAY contain bounded
diagnostic metadata after acquisition, but stale metadata does not imply a live
owner. Metadata MUST omit credentials and physical paths.

Abrupt process termination MUST release kernel-held locks. Tests MUST cover
normal completion, exception, cancellation, timeout, and child-process failure.

## Step dependencies and failure isolation

The job registry distinguishes:

- **required predecessor:** the dependent step does not run if the predecessor
  fails;
- **independent:** later work may run after failure; and
- **cleanup/finalization:** always runs but cannot convert failure to success.

The orchestrator MUST continue only steps marked independent. A failed
Treasury-evidence refresh, for example, may be independent of preserving a raw
option capture only if the option contract explicitly stores missing/stale rate
evidence and the registry records that decision. Dependency status MUST not be
inferred ad hoc from step order.

Every attempted step receives its own outcome. Any failed attempted step makes
the aggregate job nonzero/incomplete. Per-symbol or per-scope failures MUST be
counted and MUST NOT exit zero merely because some items succeeded.

Automatic replay of an entire partially successful bundle is prohibited. A
replay can duplicate immutable captures even when storage is idempotent at a
lower grain. Retry only the explicitly safe failed item or wait for the next
normal cadence.

## Network and transaction rules

- Provider calls, sleeps, backoff, decompression, parsing, canonicalization,
  and bounded candidate staging occur before physical write-lock acquisition
  and outside SQLite write transactions.
- A collector MUST NOT hold a physical write lock during provider calls, retry
  delays, or response parsing.
- If a request depends on current store state, the collector reads a bounded
  read-only snapshot before fetch and revalidates every identity-bearing
  premise after acquiring the complete write-lock set.
- After locks are acquired, the collector performs the authoritative semantic
  identity comparison against current state. Unchanged work releases the locks
  without opening a write transaction.
- The first database write follows successful locked revalidation and semantic
  identity comparison.
- Write transactions contain only the bounded append/version/membership/run
  operations needed to publish one accepted unit.
- A partial response never creates deletion tombstones.
- Commit/rollback is explicit; an exception rolls back the active unit.
- Cross-store work is not a distributed transaction. Each store records its own
  outcome and provenance, and the aggregate receipt reports partial commit if a
  later store fails.

Fetched candidates remain bounded immutable in-memory values or exact private
staging artifacts until publication. Staging is not authoritative. After lock
acquisition, a stale premise causes re-planning, a safe no-write outcome, or a
failed receipt; it is never published merely because the earlier fetch
succeeded.

## Semantic `if-new` behavior

`if-new` is a collector semantic contract, not a scheduler timestamp check.
The collector compares a versioned canonical content identity after a valid
response and before starting a write run.

When content is unchanged, the collector MUST produce zero:

- ingestion-run rows;
- raw artifact rows;
- snapshot rows;
- snapshot-membership rows; and
- canonical fact/version rows.

The scheduler receipt records `unchanged` without manufacturing a database
run. Changed or corrected content follows append-only/versioned storage.
Authentication errors, schema changes, partial pages, timeouts, HTTP 429, and
empty error bodies are failures or retryable outcomes, not unchanged evidence.

## Retry policy

Retry classification is explicit per step and error class.

- Transient transport failures, selected 5xx responses, and rate limits MAY be
  retried with a bounded attempt count, exponential backoff, jitter, and a
  total elapsed deadline.
- `Retry-After` MUST be honored when valid and within the job deadline.
- Authentication, authorization, schema/contract, invariant, checksum, and
  configuration failures MUST NOT loop.
- Retry state MUST not retain an open SQLite transaction.
- The final receipt records attempts and terminal classification, never a
  credential-bearing URL or provider body.

## Run receipts

Each non-dry attempted job MUST produce one private versioned receipt even when
no database row is written. Receipts are operational evidence, not a substitute
for canonical ingestion provenance.

A receipt MUST include:

- receipt schema version and stable run ID;
- job ID/version and plan digest;
- trigger kind: manual or external scheduler;
- requested and effective start time, finish time, and timezone;
- calendar decision and skip reason;
- logical stores and sanitized physical lock aliases;
- lock acquisition order, wait durations, and result;
- step outcomes, attempts, elapsed times, timeout flags, and dependency skips;
- semantic outcome: changed, unchanged, skipped, partial, failed, or succeeded;
- committed store aliases and ingestion-run IDs when created;
- aggregate exit code;
- code/source revision when available; and
- warnings and bounded error codes/messages.

The receipt MUST be written under a private state directory with restrictive
permissions. It MUST be staged to a unique file, flushed as required by the
platform, and atomically renamed to its final immutable name. Existing receipts
MUST NOT be overwritten. Receipt retention and archival are explicit policies.

## Exit codes

The wrapper and orchestrator MUST preserve one documented process-level
mapping. The proposed minimum mapping is:

| Code | Meaning |
| ---: | --- |
| 0 | All attempted steps succeeded, were safely unchanged, or were intentionally calendar-skipped. |
| 64 | Invalid job, arguments, plan, or dry-run configuration. |
| 69 | Required provider or store is unavailable and the failure is not a lock timeout. |
| 70 | Internal invariant, schema, checksum, or unexpected orchestration failure. |
| 74 | Local I/O or receipt publication failure. |
| 75 | Temporary failure, including lock-acquisition timeout or exhausted transient retry. |
| 78 | Missing or invalid required configuration/credential. |
| 124 | A child step exceeded its declared execution timeout. |

If several failures occur, the aggregate MUST use a deterministic precedence
defined in the registry while preserving every step's original code in the
receipt. External schedulers MUST receive the aggregate code unchanged.

## Private logs

- State and log directories use restrictive permissions.
- Logs are timestamped, bounded, and retained for a documented period.
- Log writes do not echo the environment or command lines containing secrets.
- Query strings, authorization headers, provider bodies, raw artifacts, local
  database paths, and credentials are prohibited.
- Messages use structured event names and bounded fields.
- Log-pipeline failure is visible and cannot silently convert a failed job to
  success.
- Monthly success markers are written atomically only after the complete
  monthly bundle succeeds; dry runs and partial runs never create them.

## Backup and export coordination

Raw copies of live WAL-mode SQLite files are prohibited. Backup processes use
SQLite's online backup mechanism or an equivalent reviewed consistent snapshot.
Any operation requiring writer non-overlap resolves and acquires the same
physical-store locks in deterministic order.

Analytical and Atlas export behavior is specified separately in
[Analytical Exports](ANALYTICAL_EXPORTS.md). An export receipt MUST not be
represented as a collector success receipt.

## Acceptance tests

All acceptance tests are offline, use temporary state and database paths, and
mock subprocesses/providers. They MUST NOT inspect or mutate live scheduler
state.

### Plan and dry-run tests

- Every proposed job resolves to a deterministic step graph and declared store
  set.
- Pure dry run performs no network call, database open, lock acquisition,
  marker write, collector execution, or scheduler mutation.
- Dry-run output is deterministic, strict JSON or equivalently structured,
  bounded, and path/credential free.
- Invalid jobs, missing declarations, undeclared write stores, dependency
  cycles, invalid calendars, and unbounded retries fail closed.

### Path and lock tests

- Four distinct paths produce four identities.
- One physical path configured for multiple domains is rejected before work.
- Relative, normalized, and symlink aliases collapse to one identity.
- Unsupported partial aliasing and detected hard-link aliases fail closed.
- `options-close` resolves distinct macro and market paths, sorts them, and
  acquires both before any step.
- Two processes requesting the same lock serialize; disjoint stores may run
  concurrently.
- Opposite logical store order cannot deadlock because digest order is fixed.
- Mid-acquisition failure releases already-held locks and runs no step.
- Exception, cancellation, timeout, and killed-child paths release all locks.

### Transaction and semantic tests

- Instrumented collectors prove that network calls and retry sleeps never occur
  inside a SQLite transaction or while a physical write lock is held.
- Changed input publishes in a short explicit transaction and preserves
  provenance.
- A second identical `if-new` run produces zero database writes.
- Partial, error, timeout, and 429 fixtures produce no tombstones and no
  unchanged classification.
- Failure injection before commit rolls back the unit; failure after one store
  commits is reported as partial and never described as atomic cross-store
  success.

### Failure, receipt, and logging tests

- Required-predecessor, independent, and cleanup edges behave as declared.
- Any attempted step failure makes the aggregate nonzero; per-item failures
  cannot exit zero.
- Exit-code precedence and propagation are deterministic.
- Retry counts, deadlines, `Retry-After`, and non-retryable classes are bounded.
- Receipts are schema-valid, atomically published, immutable, and complete for
  success, unchanged, skip, partial, timeout, lock failure, and configuration
  failure.
- Logs and receipts contain no secrets, environment dump, raw path, SQL, raw
  artifact, provider body, or credential-bearing URL.

### Scheduling readiness evidence

Before an external schedule can be proposed:

- all prior stages have recorded passing evidence;
- exact task definitions are reconciled with the recovered cadence table;
- user identity, WSL distribution, working directory, timezone/DST behavior,
  IgnoreNew semantics, task timeout, and exit-code propagation are reviewed;
- backup and restore drills use only non-production copies; and
- installation, update, removal, and immediate start remain separate explicit
  approvals.

## Stage 7 implemented rehearsal evidence

The primary Stage 7 gate is recorded in
[Stage 7 acceptance evidence](STAGE7_EVIDENCE.md). It validates all eight
path-free dry-run plans, all-candidate preparation before locks, complete
ordered lock acquisition, reuse of a held lock capability by publication,
bounded retry and active timeout behavior, dependency and aggregate exit-code
semantics, immutable private logs/markers/receipts with the receipt published
last, real changed then unchanged fixture execution, and WAL-safe
backup/restore equality.

The restricted wrapper at `quant_data.operations.manual_job` accepts only
explicit project, store, and private-state roots plus a registered job ID. Its
dry-run performs zero store, state, lock, provider, or scheduler activity; its
non-dry path requires already initialized synthetic fixture stores and
propagates the job receipt exit code unchanged. It is not a provider or
scheduler interface.

The primary gate passed 277 offline tests, including 43 operations tests and
three deterministic Stage 7 integration checks. Independent SolUltra
verification passed on 2026-08-11 with no findings after rerunning the full 277
tests. Scheduler readiness, external task definitions, timezone/DST, user
identity, installation, removal, and immediate start remain unresolved and
unauthorized.

## Change control and escalation

Changes to job/store ownership, lock identity, lock ordering, calendars,
semantic no-write rules, retry classes, receipts, exit codes, or scheduler
definitions require integration-owner and operations review.

Stop and escalate if a job touches an undeclared store, a physical path cannot
be resolved unambiguously, partial aliasing appears, a collector requires a
transaction across network I/O, a retry would replay successful immutable
captures, or validation would require live scheduler or database mutation.
