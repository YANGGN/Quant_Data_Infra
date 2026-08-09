# ADR 0008: Use Fresh Store-Local Reconstruction Migrations

## Status

Accepted

## Context

The recovery evidence in [`plan.md`](../../plan.md) preserves a global-looking
semantic sequence from `0000` through `0031`, but the original SQL bytes,
checksums, complete constraints, and store-local ledgers were lost. Reusing
those ordinals for guessed SQL would falsely claim byte-exact recovery and
would make later reconciliation with a surviving database ambiguous.

The accepted architecture instead operates four independent SQLite stores.
Stage 1 needs a small, reviewable schema before any historical database or live
provider is introduced.

## Decision

Create new migrations with store-qualified identities and store-local ordinals.
The first Stage 1 resources are frozen in the
[migration reconstruction map](../rebuild/MIGRATION_RECONSTRUCTION.md). Their
SQL bytes and SHA-256 values are new implementation evidence and are never
labelled recovered historical checksums.

Every mapping records:

- owning store, local ordinal, immutable resource path, and SHA-256;
- reconstruction state (`unresolved`, `fixture_validated`, or
  `recovered_exact`);
- the recovered semantic scopes that informed the new resource; and
- explicit exclusions so a narrow fixture schema is not represented as full
  `0000`-`0031` parity.

Stage 1 resources begin `unresolved`. They may move to `fixture_validated` only
after clean initialization, checksum-tamper, replay, correction, revision,
point-in-time, and deterministic-rebuild evidence passes. None of these new
resources may use `recovered_exact`.

The canonical registry is `config/system_registry.json`. It is the single
owner of active migration order and hashes. Store-local `schema_migrations`
rows are runtime evidence reconciled against that registry.

## Consequences

- The rebuild can progress honestly without inventing erased migration bytes.
- Each store initializes independently and later migrations can evolve without
  a global ordinal collision.
- A legacy semantic cross-reference remains reviewable, but it cannot be used
  as an executable migration plan.
- A future surviving database requires an explicit import/reconciliation path;
  it cannot be opened as a Stage 1 operational store.

## Alternatives

### Recreate `0000` through `0031` from prose

Rejected because semantic descriptions do not recover byte-exact DDL,
constraints, triggers, order per store, or checksums.

### Start at global ordinal `0032`

Rejected because it implies the erased `0000`-`0031` migrations were applied
and verified in each new store.

### Use schema creation code without immutable resources

Rejected because applied schema identity, tamper detection, deterministic
rebuild, and review would be weaker.

## Acceptance evidence

- The reconstruction map is reviewed before a resource becomes active.
- Registry migration order is total within each store and all referenced bytes
  match their declared SHA-256.
- Clean and repeated initialization of four explicit temporary stores passes.
- A changed applied resource fails closed before a later migration runs.
- No Stage 1 resource or ledger claims `recovered_exact` or full historical
  parity.
- The legacy semantic cross-reference covers every scope used by Stage 1 and
  clearly leaves the remaining recovered sequence unallocated.
