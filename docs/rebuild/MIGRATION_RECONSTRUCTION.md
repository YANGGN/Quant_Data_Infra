# Stage 1 Migration Reconstruction Map

Status: Implemented
Decision date: 2026-08-09
Authority: [ADR 0008](../adr/0008-fresh-store-local-reconstruction-migrations.md)

Evidence: [Stage 1 acceptance evidence](STAGE1_EVIDENCE.md)

## Purpose

This is the reviewed allocation gate for Stage 1 migration resources. It maps
new store-local identities to recovered semantic evidence without claiming the
lost SQL or checksums were recovered. No other migration ID or resource may be
allocated during Stage 1 without updating this map first.

## Frozen Stage 1 allocation

| ID | Store | Local ordinal | Immutable resource | SHA-256 status | Reconstruction state |
| --- | --- | ---: | --- | --- | --- |
| `market:0001_foundation` | market | 1 | `quant_data/migrations/market/0001_foundation.sql` | `55cdbe19de5961dce2938678cd38ccbd44c9f7ec517c09967485bda25c2e1550` | `fixture_validated` |
| `market:0002_vertical_slice` | market | 2 | `quant_data/migrations/market/0002_vertical_slice.sql` | `7faad5928d0b2f32443e7db8a3bcd7b59f8e42b1e36804ec85ed16f675bc5163` | `fixture_validated` |
| `macro:0001_foundation` | macro | 1 | `quant_data/migrations/macro/0001_foundation.sql` | `f22517e930277df4fb157ce47ba982d94fefcaf21c06e5b0d21af3b8f1920395` | `fixture_validated` |
| `macro:0002_vertical_slice` | macro | 2 | `quant_data/migrations/macro/0002_vertical_slice.sql` | `f710ebc7593ad8d78ba06c959fc745dfa9e051172340e7f8b9a70f7826cfe7a4` | `fixture_validated` |
| `company:0001_foundation` | company | 1 | `quant_data/migrations/company/0001_foundation.sql` | `081c609509c4337b74a706739c0506aa6beefb0a4de430801619781b9aad7915` | `fixture_validated` |
| `news:0001_foundation` | news | 1 | `quant_data/migrations/news/0001_foundation.sql` | `521f54561714ba9b3c67dec4b6c9eaf703ec521a8b1250193ffb40c9e9eb1097` | `fixture_validated` |

These digests identify the reviewed new resource bytes and match the canonical
registry. The complete Stage 1 fixture gate promoted the declarations to
`fixture_validated`. These new resources can never become `recovered_exact`.

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
