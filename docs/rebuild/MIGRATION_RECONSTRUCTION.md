# Migration Reconstruction Map

Status: Stage 1 through bounded Stage 4 implemented, fixture-validated, and independently verified; 30 resources allocated; Stage 5 is next
Decision date: 2026-08-09
Authority: [ADR 0008](../adr/0008-fresh-store-local-reconstruction-migrations.md)

Evidence: [Stage 1 acceptance evidence](STAGE1_EVIDENCE.md),
[Stage 2 acceptance evidence](STAGE2_EVIDENCE.md), and
[Stage 3 acceptance evidence](STAGE3_EVIDENCE.md), and
[Stage 4 acceptance evidence](STAGE4_EVIDENCE.md)

## Purpose

This is the reviewed allocation gate for rebuild migration resources. It maps
new store-local identities to recovered semantic evidence without claiming the
lost SQL or checksums were recovered. No migration ID or resource may be
allocated without updating this map first.

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

## Frozen Stage 2 allocation

Stage 2 adds one forward-only control-plane resource to each operational store.
The resources establish the same artifact, snapshot, quality-result, run-output,
and failure-audit conventions without replacing the market- and macro-specific
evidence tables already validated in Stage 1. Company and news remain empty
domain stores. They also use a forward-preserving table rebuild to align the
physical layer constraint exactly with `evidence`, `canonical`, and `derived`.

| ID | Store | Local ordinal | Immutable resource | SHA-256 status | Reconstruction state |
| --- | --- | ---: | --- | --- | --- |
| `market:0003_control_plane` | market | 3 | `quant_data/migrations/market/0003_control_plane.sql` | `144a0daf4926eb43a1d59e2aa23d190f2becd235ffa098267f972bc62c7a056b` | `fixture_validated` |
| `macro:0003_control_plane` | macro | 3 | `quant_data/migrations/macro/0003_control_plane.sql` | `144a0daf4926eb43a1d59e2aa23d190f2becd235ffa098267f972bc62c7a056b` | `fixture_validated` |
| `company:0002_control_plane` | company | 2 | `quant_data/migrations/company/0002_control_plane.sql` | `144a0daf4926eb43a1d59e2aa23d190f2becd235ffa098267f972bc62c7a056b` | `fixture_validated` |
| `news:0002_control_plane` | news | 2 | `quant_data/migrations/news/0002_control_plane.sql` | `144a0daf4926eb43a1d59e2aa23d190f2becd235ffa098267f972bc62c7a056b` | `fixture_validated` |

These IDs, stores, ordinals, resource paths, and reviewed SHA-256 values are
frozen. The SQL is a deliberate Stage 2 reconstruction of shared semantics from
recovered migration `0000`; it is never `recovered_exact`. The canonical
registry and complete Stage 2 fixture gate promoted all four rows to
`fixture_validated`.

## Frozen Stage 3 allocation

Stage 3 adds three market and eight macro forward-only resources. They restore
only the bounded offline fixture contracts recorded in the Stage 3 evidence;
they do not recover lost SQL bytes, checksums, indexes, or trigger parity.

| ID | Store | Local ordinal | Immutable resource | SHA-256 status | Reconstruction state |
| --- | --- | ---: | --- | --- | --- |
| `market:0004_instrument_catalog` | market | 4 | `quant_data/migrations/market/0004_instrument_catalog.sql` | `1650ae5fa2570cbf7b774063286e6b6307a27926c22ea539bf9264bd930a179a` | `fixture_validated` |
| `market:0005_instrument_classifications` | market | 5 | `quant_data/migrations/market/0005_instrument_classifications.sql` | `78e60168af27f46644f4e6761547f50fa6d3a2b28de83f8b1be15996a96853f3` | `fixture_validated` |
| `market:0006_controlled_universes` | market | 6 | `quant_data/migrations/market/0006_controlled_universes.sql` | `73ed3061ebc3b9441c4f43596f7b0d4ea361a9f440dc4994d5b29c5eb83ae189` | `fixture_validated` |
| `macro:0004_stage3_core` | macro | 4 | `quant_data/migrations/macro/0004_stage3_core.sql` | `f9b25365a0f7358b6206c0d279da52983a95bb90cf674daaa6c88c2d207e3ca4` | `fixture_validated` |
| `macro:0005_gdp_vintages` | macro | 5 | `quant_data/migrations/macro/0005_gdp_vintages.sql` | `f64e341853d79b91389eb6a249614cfb8f758efbeb1af9e9d89f9ed524c16627` | `fixture_validated` |
| `macro:0006_treasury_yield_curves` | macro | 6 | `quant_data/migrations/macro/0006_treasury_yield_curves.sql` | `87b98c423042d6feb9e9833725a5d218e7a9c990aa9f7902f4ee1e9779472319` | `fixture_validated` |
| `macro:0007_economic_calendar` | macro | 7 | `quant_data/migrations/macro/0007_economic_calendar.sql` | `a4d4e164da6aa6b142272e781ff4f20a13e702332819bf1c37df18dbf02fee49` | `fixture_validated` |
| `macro:0008_soma_summary_only` | macro | 8 | `quant_data/migrations/macro/0008_soma_summary_only.sql` | `6bd3b5e34bb459748dc08e7a13efbeecc61f004f6eaddeef81360e9292bc2c91` | `fixture_validated` |
| `macro:0009_eia_electricity_retail` | macro | 9 | `quant_data/migrations/macro/0009_eia_electricity_retail.sql` | `9cfb1f981eb96e387795f0410e4d0f952970d93be06b76f064d8efe5003d5b2b` | `fixture_validated` |
| `macro:0010_eia_weekly_fundamentals` | macro | 10 | `quant_data/migrations/macro/0010_eia_weekly_fundamentals.sql` | `31ec86723d23cab200c0acfae2c16a698a81bd2a4795ab32984cbf59ac83d898` | `fixture_validated` |
| `macro:0011_us_recession_periods` | macro | 11 | `quant_data/migrations/macro/0011_us_recession_periods.sql` | `f8551fbe57ce8973c13f8c13146e8ee96c50d3a2de451166e03a57dca114a322` | `fixture_validated` |

The canonical registry and complete Stage 3 primary fixture gate promoted these
eleven deliberate forward reconstructions to `fixture_validated`; independent
verification subsequently passed. Neither status changes the fact that these
resources are not `recovered_exact`.

## Frozen Stage 4 allocation

Stage 4 allocates two market option resources, five company resources including
the filing-issuer view reconstruction, and two news resources. The recovered
semantic sequence constrains their order, names, and scope. It does not recover
their historical SQL bytes, checksums, indexes, or trigger text. The complete
offline Stage 4 primary fixture gate and independent SolUltra verification
passed and promoted every row to `fixture_validated`. Stage 5 is the next
authorized executable stage.

| ID | Store | Local ordinal | Immutable resource | SHA-256 status | Reconstruction state |
| --- | --- | ---: | --- | --- | --- |
| `market:0007_options_core` | market | 7 | `quant_data/migrations/market/0007_options_core.sql` | `2639bae26823eef9ad8fe2200039058763736fbd237051fd129ba3d6763d8844` | `fixture_validated` |
| `market:0008_option_surface_inputs` | market | 8 | `quant_data/migrations/market/0008_option_surface_inputs.sql` | `035a2c6e6495e35c293faf9593456d45bdc1c6d820509adf0e4c6e6fd01f4db1` | `fixture_validated` |
| `company:0003_sec_core` | company | 3 | `quant_data/migrations/company/0003_sec_core.sql` | `b2ca550918c658ef8f432de6ff6a935e3b24a2cd0a3e6a4c5ff96958f49a688e` | `fixture_validated` |
| `company:0004_corporate_actions` | company | 4 | `quant_data/migrations/company/0004_corporate_actions.sql` | `9623793c09ebe9e802a40c7a55f75c4caa4707a44c30400a449b61c5a38838c0` | `fixture_validated` |
| `company:0005_corporate_action_integrity` | company | 5 | `quant_data/migrations/company/0005_corporate_action_integrity.sql` | `5b59d6257a7d459aa499b8ad6ab0e4a9489ae8df2d90dc18b2839c73e6bf77cd` | `fixture_validated` |
| `company:0006_earnings_expectations` | company | 6 | `quant_data/migrations/company/0006_earnings_expectations.sql` | `6ac7c533a3d9bd6bfc3ef4a4ede89491c542ee331089d7c903835a840bb0962b` | `fixture_validated` |
| `company:0007_filing_issuer_view` | company | 7 | `quant_data/migrations/company/0007_filing_issuer_view.sql` | `03fb82fa7a2d3e741dac32422899ad9b7635d667cdab1887857c35c0c52ebdd7` | `fixture_validated` |
| `news:0003_immutable_items` | news | 3 | `quant_data/migrations/news/0003_immutable_items.sql` | `0ab1f4b2b4dcc0350cc263285e6d2b45f482c985d8e8f8ea45dfa4bc72ffd81e` | `fixture_validated` |
| `news:0004_search_index` | news | 4 | `quant_data/migrations/news/0004_search_index.sql` | `fdc8e4b8ef838a3f61134e867c15391b8a7db31ebc492242b0a4d87218658402` | `fixture_validated` |

The market rows map respectively to recovered migrations `0029` and `0030`.
The company rows map to recovered migrations `0024`, `0025`, `0026`, `0028`,
and `0031`; `company:0007_filing_issuer_view` intentionally creates the
recovered derived view rather than a guessed join table. The news rows are
deliberate Stage 4 reconstructions of the accepted immutable evidence,
versioning, and derived-search contracts; no recovered migration number or SQL
parity is claimed for them.

The complete offline Stage 4 primary fixture gate records these nine rows as
`fixture_validated`; independent SolUltra verification passed. Stage 5 is the
next authorized executable stage.

## Legacy semantic cross-reference

| New resource | Recovered evidence used | Deliberate stage boundary |
| --- | --- | --- |
| `market:0001_foundation` | `0000` shared ledger and ingestion/provenance semantics | Reconstructed minimum shared control plane only |
| `market:0002_vertical_slice` | `0001` instrument identity, `0002` daily prices, `0014` price correction versions, `0020` price ingestion requests | Synthetic daily-price slice only; no classifications, history registry, corporate actions, or options |
| `macro:0001_foundation` | `0000` shared ledger and ingestion/provenance semantics | Reconstructed minimum shared control plane only |
| `macro:0002_vertical_slice` | `0006` macro schema, `0007` revision-aware macro, `0013` snapshot membership, `0019` macro history registry | Synthetic EMPLOY-style release/vintage slice only; no GDP, Treasury, SOMA, EIA, recession, or calendar restoration |
| `company:0001_foundation` | `0000` shared ledger and ingestion/provenance semantics | Foundation only; no company facts |
| `news:0001_foundation` | `0000` shared ledger and ingestion/provenance semantics | Foundation only; no news facts |
| `market:0003_control_plane` | `0000` shared control-plane semantics | Shared artifact/snapshot/quality/run-audit control plane only; no Stage 3 market restoration |
| `macro:0003_control_plane` | `0000` shared control-plane semantics | Shared artifact/snapshot/quality/run-audit control plane only; no Stage 3 macro restoration |
| `company:0002_control_plane` | `0000` shared control-plane semantics | Empty company-domain foundation only |
| `news:0002_control_plane` | `0000` shared control-plane semantics | Empty news-domain foundation only |
| `market:0004_instrument_catalog` | `0001` instrument identity and `0002` dated provider identifiers | Fixture catalog evidence and dated identifier contract only; no recovered DDL parity |
| `market:0005_instrument_classifications` | `0004` classifications | Append-only effective-dated classification fixture contract only |
| `market:0006_controlled_universes` | `0001` and `0002` market identity context | Controlled complete-scope membership, tombstone, and restoration contract only |
| `macro:0004_stage3_core` | `0006`, `0007`, and `0013` macro catalog, observation, and snapshot semantics | Generic fixture catalog/dimensions/current-observation lineage only |
| `macro:0005_gdp_vintages` | `0003` and `0016` GDP/revision semantics | GDP vintage provenance fixture contract only |
| `macro:0006_treasury_yield_curves` | `0005`, `0015`, and `0021` Treasury semantics | Current-state and correction fixture contract only |
| `macro:0007_economic_calendar` | `0012` economic-calendar semantics | Calendar identity and correction fixture contract only |
| `macro:0008_soma_summary_only` | `0008`, `0010`, and `0011` SOMA semantics | Aggregate-only evidence and summaries; explicitly no CUSIP-level holdings |
| `macro:0009_eia_electricity_retail` | `0009`, `0017`, and `0023` EIA retail semantics | Evidence, scope, version, tombstone, restoration, and current-pointer fixture contract only |
| `macro:0010_eia_weekly_fundamentals` | `0022` EIA weekly semantics | Content identity, versions, and snapshot-membership fixture contract only |
| `macro:0011_us_recession_periods` | `0027` U.S. recession chronology | Completed-period chronology fixture contract only |

All other recovered scopes remain unallocated after bounded Stage 4. The exact
market options ownership at `0029`/`0030` and the filing-issuer derived-view
invariants at `0031` are represented by the frozen Stage 4 resources above,
without asserting historical SQL parity.

## Activation checklist

1. Review final UTF-8/LF SQL bytes and compute lowercase SHA-256.
2. Declare the same reviewed value in `config/system_registry.json`.
3. Verify registry/resource/order integrity before opening a writer.
4. Apply only to four explicit temporary paths during the Stage 1, Stage 2,
   Stage 3, and Stage 4 fixture gates.
5. Run initialization, rerun, tamper, wrong-store, partial-failure, and
   source/backup/restored evidence tests.
6. During a forward-preserving table rebuild, temporarily disable foreign-key
   enforcement only inside the atomic rebuild and require `foreign_key_check`
   before the migration ledger advances.
7. Promote reconstruction state to `fixture_validated` only with the complete
   applicable stage evidence. The 30 currently allocated resources have passed
   their applicable primary fixture gates; none can be relabeled
   `recovered_exact`.
8. Stage 4 independent verification completed `G4`; any future migration
   allocation remains serialized under the next applicable roadmap gate.
