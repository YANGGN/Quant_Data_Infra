# ADR 0006: Lock Writers by Resolved Physical Store

## Status

Accepted

## Context

The system supports four logical stores: market, macro, company, and news, and
requires four distinct physical files. A recovered job such as `options-close`
may still touch more than one store.

A lock named only for a job or logical domain is therefore unsafe:

- textual or symlink aliases can otherwise bypass intended coordination; and
- one job-level lock does not synchronize with other writers of its second
  store.

SQLite's own writer serialization and busy timeout do not express job
ownership, prevent expensive overlap, coordinate consistent exports, or give a
bounded operational failure mode.

Related documents:

- [Architecture](../../ARCHITECTURE.md)
- [Scheduling and locking specification](../rebuild/SCHEDULING_AND_LOCKING.md)
- [System registry specification](../rebuild/SYSTEM_REGISTRY_SPEC.md)
- [Data and time contracts](../rebuild/DATA_AND_TIME_CONTRACTS.md)
- [Roadmap](../../ROADMAP.md)

## Decision

All cooperating writers and non-overlap operations will lock by a versioned
identity derived from the host-resolved canonical SQLite path.

The host will resolve every logical store a job may write before execution. It
will normalize and alias-check those paths without creating or initializing a
database. The lock key will be the full SHA-256 digest of a versioned namespace
and canonical path URI. Configured aliases are detected and rejected; distinct
store paths remain independently lockable.

A multi-store job will compute its complete lock set before work, deduplicate
it, sort canonical physical path URIs deterministically, acquire their digested
lock keys in that order under one overall timeout after network fetch and
candidate staging, and release in reverse order after the bounded
validation/publication phase. It will not run a subset when acquisition fails
and will not upgrade locks after publication starts. Network calls and retry
delays will not run while a physical write lock is held.

The kernel advisory lock, not lock-file existence or metadata, establishes
ownership. Every error, cancellation, timeout, and child-process failure path
will release acquired locks.

Job-to-store declarations and the lock algorithm version will live in the
system registry. The scheduler, manual wrapper, backup coordination, and
exports requiring non-overlap will use the same resolver and lock directory.

## Consequences

### Positive

- All four store roles follow one synchronization rule.
- Multi-store jobs synchronize with single-store jobs correctly.
- Deterministic ordering prevents lock-order deadlocks among cooperating
  processes.
- Disjoint split stores can still run concurrently.
- Lock identity is decoupled from mutable job names and schedules.
- Dry-run plans can show sanitized lock aliases without exposing paths.

### Negative

- Path canonicalization must handle symlinks, case rules, and not-yet-created
  files consistently.
- All cooperating processes must share the algorithm version and lock
  directory.
- Multi-store publication may hold several store locks during bounded locked
  revalidation and short writes, temporarily reducing split-store concurrency.
- Undisclosed hard-link aliases cannot be protected by path identity and must
  be prohibited or detected among configured paths.

### Risks and mitigations

- **Risk:** two textual aliases generate different locks. **Mitigation:**
  resolve existing targets, canonicalize the nearest existing parent for new
  targets, compare configured paths with operating-system same-file checks, and
  test symlink aliases.
- **Risk:** sensitive paths leak through lock names or logs. **Mitigation:** use
  full digests as filenames and sanitized aliases in output.
- **Risk:** two jobs deadlock on market and macro in opposite order.
  **Mitigation:** sort the complete deduplicated digest set before acquisition;
  lock upgrades are forbidden.
- **Risk:** a process leaves apparent stale ownership. **Mitigation:** rely on
  kernel advisory locks; metadata is diagnostic only.
- **Risk:** partial aliasing is mistaken for a supported schema layout.
  **Mitigation:** reject it independently of the lock resolver unless another
  architecture decision accepts it.

## Alternatives

### Lock by logical domain

Rejected because textual and symlink aliases can refer to one physical file,
and multi-store jobs can touch more than one domain.

### Lock by job name

Rejected because different jobs can write the same store and one job can write
several stores.

### Use one global writer lock

Safe but not selected as the target because it needlessly serializes disjoint
split stores. It MAY be used as a temporary fail-safe during early manual
rehearsal, but not represented as final parity.

### Depend only on SQLite busy timeout

Rejected because it does not coordinate whole job intent, multi-store order,
consistent export windows, or bounded pre-execution rejection.

### Lock by device and inode

Rejected as the stable key because atomic replacement changes inode identity
while the configured authority remains the path. Same-file checks remain useful
for detecting configured aliases of existing files.

## Acceptance evidence

This decision is accepted only when offline multi-process tests prove:

- distinct split paths can proceed independently;
- duplicate physical store identities fail validation before work begins;
- normalized and symlink aliases resolve to one identity;
- unsupported partial aliasing and detected hard links fail closed;
- a macro-plus-market job sorts its distinct physical paths;
- opposite logical request order cannot deadlock;
- one overall timeout bounds partial multi-lock acquisition;
- acquisition failure starts no work and releases prior locks;
- normal, exception, cancellation, timeout, and child-death paths release all
  locks;
- dry runs reveal no raw paths and acquire no lock; and
- the scheduler, backup/export coordination, and manual wrapper produce the
  same lock identities from the same temporary configuration.
