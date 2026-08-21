# ADR 0010: Stage 12D No-Transfer Market Adoption

## Status

Accepted — 2026-08-16

## Context

ADR 0009 selected project-root-relative "data/market.sqlite" as the canonical
market path, while deliberately leaving a later operational cutover gate open.
Its prospective consequence described a future transfer-oriented cutover with a
physical lock and SQLite online backup.

Stage 12C is now complete as a bounded no-copy two-session population. Its
private receipt binds registry 2.14.0, schema 1.8.0, the frozen 629-symbol
scope, and the final project-local market state. The project-local database is
already the approved, verified state; a second multi-gigabyte database transfer
would add risk without adding an approved data outcome.

The user has authorized Stage 12D only as a refined project-local
adoption/freeze proof. This decision does not make the market store public or
authorize an ongoing operation.

## Decision

Stage 12D adopts no transfer, copy, move, replacement, promotion, or backup
operation. The only database it may inspect is the existing canonical target
resolved from the explicit project root:

    data/market.sqlite

The proof is a fixed, zero-argument runner. It cannot accept a caller-selected
path or SQL. Before inspection it must bind a direct regular, non-symlink,
single-link target and record its physical identity. It may open that one file
only with SQLite mode=ro&immutable=1 and query_only enabled, after proving the
required zero-or-absent WAL and rollback-journal precondition.

Exactly two serial manual proof invocations are permitted after their offline
and independent gates pass. Each successful proof may append only one private
small receipt created with O_EXCL and mode 0600 beneath the fixed Stage 12D
evidence root with mode 0700. No pointer, promotion marker, database write,
backup artifact, or other durable output is permitted.

The two receipts must have distinct immutable invocation identities while
producing the same path-, secret-, and raw-body-free semantic proof digest.

## Supersession

This ADR supersedes **only** the obsolete transfer-oriented consequence in ADR
0009 and any corresponding prospective Stage 12 wording that requires a
database copy, transfer, or SQLite online backup for the project-local market
adoption/freeze gate.

It does not supersede ADR 0009's canonical path, explicit project-root
resolution, alias rejection, override semantics, four-store ownership,
physical-store identity, or failure boundaries. It also does not change
historical Stage 9 through Stage 12C evidence. Existing documents retain their
historical text until their owning documentation lane updates their status; in
any direct conflict about a Stage 12D transfer, this ADR controls.

## Consequences

- The canonical target remains data/market.sqlite under registry 2.14.0 and
  schema 1.8.0.
- Stage 12D proves only a quiet, read-only, fixed-target adoption/freeze
  condition. It cannot alter the target or use a backup as evidence.
- The Stage 12C receipt, plan, semantic scope, final ledger, and final counts
  are frozen inputs to both manual proofs.
- A failure stops the affected proof without an automatic retry or target
  mutation.
- Stage 12E remains closed. No scheduler, public consumer, tool, dashboard,
  export, provider, credential, or network work follows from this decision.

## Non-authorization

This decision does not authorize a second roughly-six-gigabyte database,
copying, moving, replacing, compacting, restoring, promoting, retiring, or
backing up any store. It does not authorize a registry revision, migration,
retained-source reopen, provider call, credential read, network access,
database write lock, checkpoint, journal-mode change, caller SQL, public
consumer, scheduler, or operational recovery rehearsal.

## Related records

- [ADR 0009: canonical market operational path](0009-canonical-market-operational-path.md)
- [Stage 12D project-local no-transfer operationalization](../rebuild/STAGE12D_PROJECT_LOCAL_OPERATIONALIZATION.md)
- [Stage 12C evidence record](../rebuild/STAGE12C_EVIDENCE.md)
- [Stage 12C two-session Market v1 gap contract](../rebuild/STAGE12C_MARKET_GAP_V1.md)
