# Stage 1 Migration Reconstruction Map

Status: Accepted  
Decision date: 2026-08-09  
Authority: [ADR 0008](../adr/0008-fresh-store-local-reconstruction-migrations.md)

## Purpose

This is the reviewed allocation gate for Stage 1 migration resources. It maps
new store-local identities to recovered semantic evidence without claiming the
lost SQL or checksums were recovered. No other migration ID or resource may be
allocated during Stage 1 without updating this map first.

## Frozen Stage 1 allocation

| ID | Store | Local ordinal | Immutable resource | SHA-256 status | Reconstruction state |
| --- | --- | ---: | --- | --- | --- |
| `market:0001_foundation` | market | 1 | `quant_data/migrations/market/0001_foundation.sql` | Pending immutable resource review | `unresolved` |
| `market:0002_vertical_slice` | market | 2 | `quant_data/migrations/market/0002_vertical_slice.sql` | Pending immutable resource review | `unresolved` |
| `macro:0001_foundation` | macro | 1 | `quant_data/migrations/macro/0001_foundation.sql` | Pending immutable resource review | `unresolved` |
| `macro:0002_vertical_slice` | macro | 2 | `quant_data/migrations/macro/0002_vertical_slice.sql` | Pending immutable resource review | `unresolved` |
| `company:0001_foundation` | company | 1 | `quant_data/migrations/company/0001_foundation.sql` | Pending immutable resource review | `unresolved` |
| `news:0001_foundation` | news | 1 | `quant_data/migrations/news/0001_foundation.sql` | Pending immutable resource review | `unresolved` |

“Pending” is a checksum state, not permission to apply a resource. The primary
integration owner fixes each digest in this table and the canonical registry
after reviewing the final bytes and before the migration runner may apply it.
At that point the state remains `unresolved` until the Stage 1 fixture gate
passes, then becomes `fixture_validated` in both declarations. These new
resources can never become `recovered_exact`.

## Legacy semantic cross-reference

| New resource | Recovered evidence used | Deliberate Stage 1 boundary |
| --- | --- | --- |
| `market:0001_foundation` | `0000` shared ledger and ingestion/provenance semantics | Reconstructed minimum shared control plane only |
| `market:0002_vertical_slice` | `0001` instrument identity, `0002` daily prices, `0014` price correction versions, `0020` price ingestion requests | Synthetic daily-price slice only; no classifications, history registry, corporate actions, or options |
| `macro:0001_foundation` | `0000` shared ledger and ingestion/provenance semantics | Reconstructed minimum shared control plane only |
| `macro:0002_vertical_slice` | `0006` macro schema, `0007` revision-aware macro, `0013` snapshot membership, `0019` macro history registry | Synthetic EMPLOY-style release/vintage slice only; no GDP, Treasury, SOMA, EIA, recession, or calendar restoration |
| `company:0001_foundation` | `0000` shared ledger and ingestion/provenance semantics | Foundation only; no company facts |
| `news:0001_foundation` | `0000` shared ledger and ingestion/provenance semantics | Foundation only; no news facts |

All other recovered scopes from `0003` through `0031` remain unallocated for
Stage 1. In particular, the exact market options ownership at `0029`/`0030` and
the filing-issuer derived-view invariants at `0031` are preserved as later
constraints, not folded into these resources.

## Activation checklist

1. Review final UTF-8/LF SQL bytes and compute lowercase SHA-256.
2. Replace every pending checksum above and declare the same value in
   `config/system_registry.json`.
3. Verify registry/resource/order integrity before opening a writer.
4. Apply only to four explicit temporary paths during Stage 1.
5. Run initialization, rerun, tamper, wrong-store, and partial-failure tests.
6. Promote reconstruction state to `fixture_validated` only with the complete
   Stage 1 exit evidence; otherwise leave it `unresolved`.
