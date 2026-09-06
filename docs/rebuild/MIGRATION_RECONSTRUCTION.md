# Migration Reconstruction Map

Status: 38 resources allocated; the bounded Stage 1 through Stage 4 and
explicitly authorized Stage 9, Stage 10, and Stage 11 resources are
`fixture_validated`. Stage 10 retained population receipts are complete; the
Stage 11 private population and completion receipt are complete; and the
official GDP/CPI, employment-vintage, and FMP wholesale-evidence migrations
are populated in the canonical macro store. The proposed BLS CPI
original-release archive is rejected and has no allocation in this map.
Decision date: 2026-08-09
Authority: [ADR 0008](../adr/0008-fresh-store-local-reconstruction-migrations.md)

Evidence: [Stage 1 acceptance evidence](STAGE1_EVIDENCE.md),
[Stage 2 acceptance evidence](STAGE2_EVIDENCE.md),
[Stage 3 acceptance evidence](STAGE3_EVIDENCE.md),
[Stage 4 acceptance evidence](STAGE4_EVIDENCE.md),
[Stage 9 primary evidence](STAGE9_EVIDENCE.md),
[Stage 10 evidence](STAGE10_EVIDENCE.md), and
[Stage 11 evidence](STAGE11_EVIDENCE.md)

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
passed and promoted every row to `fixture_validated`. No later migration was
allocated at that time.

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
`fixture_validated`; independent SolUltra verification passed. The bounded
Stage 9 exception below is the only later migration allocation.

## Bounded Stage 9 allocation

Under ADR 0008, Stage 9 allocates exactly one additional market-local forward
reconstruction. It is deliberately limited to the exact candidate-only FMP
`SPY` slice, not recovered historical SQL, checksum, or ordinal parity. No
other future migration is allocated by this exception.

| ID | Store | Local ordinal | Immutable resource | SHA-256 | Reconstruction state | Activation status |
| --- | --- | ---: | --- | --- | --- | --- |
| `market:0009_fmp_daily_price_backfill` | market | 9 | `quant_data/migrations/market/0009_fmp_daily_price_backfill.sql` | `f2e664888449cb3d1885f1199d1a22996709cb87f4f3ac005bd8f91c0cc62ba0` | `fixture_validated` | Primary and independent offline gates passed after correction |

The canonical registry declares this same market store, ordinal, resource, and
digest immediately after `market:0008_option_surface_inputs`. Its primary
offline evidence is recorded in [Stage 9 primary evidence](STAGE9_EVIDENCE.md).
Independent re-verification passed after this allocation correction.
This row does not claim a live FMP response/data population, operational
promotion, or old-store retirement.

## Fixture-validated Stage 10 allocation

The user subsequently authorized a distinct, market-only Stage 10 profile for
current S&P 500, Nasdaq-100, and Dow 30 constituents, a reviewed ETF/index
roster, and the full daily OHLCV history returned per provider symbol. Russell
2000 constituents are explicitly excluded. The allocation is deliberately
separate from the frozen Stage 9 `SPY` relations and does not authorize an
operational promotion, scheduler, public export, tool, dashboard, or store
retirement.

| ID | Store | Local ordinal | Immutable resource | SHA-256 | Reconstruction state | Activation status |
| --- | --- | ---: | --- | --- | --- | --- |
| `market:0010_stage10_market_history` | market | 10 | `quant_data/migrations/market/0010_stage10_market_history.sql` | `a7703655f6fe8089431eacdc78600582d6f5582589c0c38b531a2b93c7dc041a` | `fixture_validated` | Offline migration and hostile gates passed; retained base and extension candidate receipts recorded |

This reviewed allocation freezes the store, ordinal, resource name, bounded
semantic purpose, and exact UTF-8/LF resource digest. The canonical registry
declares the same values. Population evidence and its historical sequencing
qualification are recorded separately in
[Stage 10 evidence](STAGE10_EVIDENCE.md).

## Private Alpaca option raw-evidence allocation

Registry `2.65.0` allocates one forward market migration for exact Alpaca
option response bytes, capture membership, and the narrowly proven correction
of explicit ordinary 100-share deliverables. It does not alter the applied
Stage 4 option migration bytes or expose raw evidence through a tool,
dashboard, export, or public route. Historical quote values that were not
retained are not reconstructed.

| ID | Store | Local ordinal | Immutable resource | SHA-256 | Reconstruction state | Activation status |
| --- | --- | ---: | --- | --- | --- | --- |
| `market:0011_option_raw_evidence` | market | 11 | `quant_data/migrations/market/0011_option_raw_evidence.sql` | `f6a4685963e1eddffb7fd92944e19e4bbeda0c89bc196d85a2308f1f408d8129` | `fixture_validated` | Applied to `data/market.sqlite` at `2026-08-31T01:42:58.896154Z`; 294 contracts corrected, 1,162 historical surfaces explicitly missing, raw relations empty pending a future capture |

The migration owns `option_raw_responses` and
`option_capture_raw_responses`. The first is content-addressed exact response
evidence; the second binds a canonical option capture to each named response
in source order. Both are immutable. The corrective statements match only an
Alpaca, 100-multiplier contract with one 100-share, 100%-allocation equity
delivery whose root and delivery symbol equal the stored canonical underlying.
All other contracts remain untouched.

## Fixture-validated Stage 11 allocation

The user has authorized one additive macro-store allocation for the bounded
BEA/EIA population that follows a completed Stage 10 candidate. It uses
dedicated non-fixture evidence and canonical relations; it does not reinterpret
or write the frozen Stage 3 fixture relations. The immutable resource digest
was recorded after byte review and before registry activation.

| ID | Store | Local ordinal | Immutable resource | SHA-256 | Reconstruction state | Activation status |
| --- | --- | ---: | --- | --- | --- | --- |
| `macro:0012_stage11_bea_eia_live_history` | macro | 12 | `quant_data/migrations/macro/0012_stage11_bea_eia_live_history.sql` | `4f29eed2d73fcb1aa6f3c519a3360c132658cf152341261a18d4332e140fd0a5` | `fixture_validated` | Schema, syntax, hostile migration, and offline gates passed; private Stage 11 population and receipt complete |

This allocation is limited to the two reviewed BEA NIPA table/series pairs,
the complete paginated U.S./all-sector EIA retail scope, and weekly series
`PET.WCESTUS1.W`. It grants no generic BEA/EIA discovery, scheduler,
operational promotion, public consumer, or store-retirement authority.

## Official GDP/CPI vintage allocation

The user authorized one isolated canonical macro-store allocation for official
BEA GDP release history and BLS CPI revision snapshots. It does not reinterpret
the Stage 3 fixture model or the retained Stage 11 candidate relations.

| ID | Store | Local ordinal | Immutable resource | SHA-256 | Reconstruction state | Activation status |
| --- | --- | ---: | --- | --- | --- | --- |
| `macro:0013_live_gdp_cpi_vintages` | macro | 13 | `quant_data/migrations/macro/0013_live_gdp_cpi_vintages.sql` | `9eab5c35c6377e6f22a927dbc4602e982ff3c8ae6095e782de546a05de58f5bc` | `fixture_validated` | Parser, migration, publisher, registry, and operation gates passed; 16 official captures populated and current-only refresh scheduled |

The allocation owns seven dedicated relations for raw evidence, four fixed
series, source-vintage releases, immutable versions, current pointers,
capture membership, and GDP provenance. It grants no broader macro discovery,
credential, public consumer, caller-selected path, or repeat of the completed
14-file CPI archive backfill.

## Official employment-vintage allocation

The user authorized one isolated successor allocation for Philadelphia Fed
RTDSM payroll/unemployment history and the two exact BLS current employment
series. It extends only the dedicated official-vintage relations created by
macro ordinal 13.

| ID | Store | Local ordinal | Immutable resource | SHA-256 | Reconstruction state | Activation status |
| --- | --- | ---: | --- | --- | --- | --- |
| `macro:0014_live_employment_vintages` | macro | 14 | `quant_data/migrations/macro/0014_live_employment_vintages.sql` | `79d1ed0a0ee7e059109bcd3238f1c7ab79148924b69c8eda282b09c0af245ee8` | `fixture_validated` | Parser, migration, publisher, registry, operation, lineage, and independent gates passed; three official captures populated and current-only BLS refresh scheduled |

The migration preserves all ordinal-13 rows and triggers while adding only the
Philadelphia Fed provider and the exact payroll/unemployment series aliases.
Provider-safe release, version, and membership triggers reject Philadelphia
Fed lineage for non-employment BLS series while preserving semantic replay.
It grants no broader RTDSM/BLS discovery, credential, public consumer,
caller-selected path, or repeat of the completed two-workbook backfill.

## Official macro-history extension allocation

| ID | Store | Local ordinal | Immutable resource | SHA-256 | Reconstruction state | Activation status |
| --- | --- | ---: | --- | --- | --- | --- |
| `macro:0015_live_macro_history_extension` | macro | 15 | `quant_data/migrations/macro/0015_live_macro_history_extension.sql` | `31153005b135f6bb03d0c228488bee73b6c50051afaaf4b7f2a9252f957158b9` | `fixture_validated` | One-time deep CPI and Philadelphia Fed output/CPI history completed; no recurring collector or public exposure added |

Macro ordinal 15 preserves all ordinal-13/14 rows while adding only the two
source-native Philadelphia Fed output series and the narrow Philadelphia Fed
CPI evidence aliases. The completed manual operation reused five sealed
responses, made six BLS requests, and added 14,577 immutable versions. It does
not reinterpret the BEA GDP series, add a scheduler, or authorize a repeat.
The exact registry projection removes this migration and its two collectors to
restore byte-exact registry `2.17.0`.

## FMP wholesale calendar evidence allocation

Registry `2.21.0` adds one forward macro allocation for immutable wholesale
FMP U.S. economic-calendar response bytes and raw-row lineage. It follows the
official macro-history extension and supports local replay of reviewed
consensus aliases without a second provider request.

| ID | Store | Local ordinal | Immutable resource | SHA-256 | Reconstruction state | Activation status |
| --- | --- | ---: | --- | --- | --- | --- |
| `macro:0016_fmp_calendar_wholesale_evidence` | macro | 16 | `quant_data/migrations/macro/0016_fmp_calendar_wholesale_evidence.sql` | `78dc02d34c0489c3f1fe4b7847870a18955606b1f47a3309ed8464ee9f3bbb4d` | `fixture_validated` | One-time wholesale calendar history and zero-network local replays complete; no public consumer or new timer |

Macro ordinal 16 preserves ordinal-13 through ordinal-15 relations while
adding only raw FMP calendar capture and row evidence. It does not add a BLS
original-release archive, surprise table, public consumer, scheduler, or
authority to repeat completed history.


## Forward macro allocations after wholesale history

The accepted GDI extension occupies ordinal 17. ADR 0012 then allocates
ordinal 18 for future compact FMP calendar persistence; it does not alter the
bytes, checksum, relations, or completed evidence of ordinal 16.

| ID | Store | Local ordinal | Immutable resource | SHA-256 | Reconstruction state | Activation status |
| --- | --- | ---: | --- | --- | --- | --- |
| `macro:0017_live_gdi_vintages` | macro | 17 | `quant_data/migrations/macro/0017_live_gdi_vintages.sql` | `e92da1620b2da10e15d76ee9a3817fc081d0363f6cd7b87653f2340a8f756e71` | `fixture_validated` | Current BEA GDP/GDI refresh population is complete under its retained evidence |
| `macro:0018_fmp_calendar_incremental_events` | macro | 18 | `quant_data/migrations/macro/0018_fmp_calendar_incremental_events.sql` | `078cfd62e7cd7a314e6b828b19414023f60fa81c760c89a31b3398f1b7e41a3e` | `fixture_validated` | Canonically applied at `2026-08-25T01:31:39.017401Z`; new relations empty and no provider operation |

Migration 0018 adds immutable changed-batch receipts, stable raw event
identities, append-only event corrections, and one replaceable
non-authoritative latest-response cache. It neither copies legacy rows into
the new relations nor authorizes deletion or compaction of migration 0016.

The user explicitly authorized the canonical application on 2026-08-24. The
reviewed one-store runner applied only the pending macro migration and
registered the two successor datasets. The bounded post-check confirmed the
exact ordinal/resource/checksum, four empty new tables, three indexes, twelve
triggers, clean integrity and foreign keys, and unchanged legacy counts of 62
captures and 47,754 rows. It made no provider, credential, history-repeat,
scheduler, or cleanup action.
## Rejected BLS CPI original-release archive candidate

The working `2.22.0` candidate declared
`macro:0017_bls_cpi_release_archive` at macro ordinal 17 with resource
`quant_data/migrations/macro/0017_bls_cpi_release_archive.sql`. It was never
allocated by this map or supported by an accepted completion record. Under
[ADR 0011](../adr/0011-retire-proposed-bls-cpi-release-archive.md), it is a
rejected candidate rather than an outstanding future allocation. It must not
be applied, assigned a later ordinal, retained as a fallback, or revived under
a renamed resource without a new explicit user decision. This does not alter
the completed `2.16.0` BLS annual-revision snapshots or their current refresh.

The explicitly authorized 2026-08-21 immutable read-only canonical check found
the exact accepted `0016` row as the macro-store head and found no `0017` ID,
ordinal, or resource collision and no archive capture, release, fact, trigger,
or index relation. Main/WAL/SHM/journal physical stamps were identical before
and after, with all sidecars absent. Ignored scratch captures and a
pre-publication backup show that the abandoned workflow was invoked, but they
do not supersede the canonical ledger result or constitute an accepted
allocation/completion record.

## Bounded FMP stock-news allocation

The user has authorized one deliberate forward reconstruction for a
manual-only FMP stock-latest news page. It is a private news-store population
contract, not recovered provider history or public news coverage. The reviewed
resource follows `news:0004_search_index`; its offline fixture gate and every
live gate remain pending.

| ID | Store | Local ordinal | Immutable resource | SHA-256 | Reconstruction state | Activation status |
| --- | --- | ---: | --- | --- | --- | --- |
| `news:0005_fmp_stock_latest` | news | 5 | `quant_data/migrations/news/0005_fmp_stock_latest.sql` | `f2565061f5b908255838be875c05112b8bf91033295b87a5b955f9de2d142217` | `fixture_validated` | Focused offline fixture gate passed; live gate pending |

The allocation retains an immutable pre-request intent, at most one terminal
outcome, and exact raw response bytes before private article/version and
capture-membership rows. It grants no scheduler, retry, fallback endpoint,
second page, public consumer, promotion, or store-retirement authority.

## Repeatable FMP current-news allocation

Registry `2.63.0` adds a separate, forward-only successor for retained current
FMP stock-news headlines.  It follows the frozen `news:0005_fmp_stock_latest`
resource without changing its one-shot scope or history.  The new migration
owns immutable hourly-slot attempt/outcome/capture evidence plus article,
version, and capture-membership lineage.  It grants no historical-completeness
claim, tombstone inference, scheduler, provider retry, or public body/raw
evidence exposure.

| ID | Store | Local ordinal | Immutable resource | SHA-256 | Reconstruction state | Activation status |
| --- | --- | ---: | --- | --- | --- | --- |
| `news:0006_fmp_stock_latest_current` | news | 6 | `quant_data/migrations/news/0006_fmp_stock_latest_current.sql` | `bf8757bcc7679d1bd57408978eed996c9f4dad9c89339a52ca8adbeedbf83d30` | `fixture_validated` | Applied; one successful 2026-08-30 bounded request retained 229 articles; no timer |

The current collector remains manual-only until a separately authorized
recurring schedule exists.

## Current multi-source news allocation

Registry `2.64.0` adds a forward current-news successor without reopening the
frozen one-shot 0005 scope. Migration 0007 owns the generic immutable evidence,
article-version, symbol, and capture-membership relations for the fixed FMP
press/general, Federal Reserve, ECB, BEA, EIA, and Alpaca/Benzinga sources.
Migration 0008 adopts the pre-registry `fmp_news_articles` table without
rewriting its 22,910 rows. The corresponding dataset is inactive private
evidence; a fail-closed schema guard verifies its exact columns before
registration, and no collector or public consumer binds to it.

| ID | Store | Local ordinal | Immutable resource | SHA-256 | Reconstruction state | Activation status |
| --- | --- | ---: | --- | --- | --- | --- |
| `news:0007_current_multi_source` | news | 7 | `quant_data/migrations/news/0007_current_multi_source.sql` | `df58936c73045ba8a382bf0a24743db5e314246e0f81d8943872b6d3e6c010c3` | `fixture_validated` | Applied; all 20 bounded requests succeeded at 2026-08-30 16:00 UTC, retaining 1,069 generic-source articles |
| `news:0008_adopt_fmp_news_legacy` | news | 8 | `quant_data/migrations/news/0008_adopt_fmp_news_legacy.sql` | `ea5302726758ab2bb987a525c3094885e016e4596689d26c8290808523f8327f` | `fixture_validated` | Applied; existing rows preserved; inactive/private/no consumer |

Coverage derives from retained market equity, ETF, and index symbols. Alpaca
receives only equity and ETF symbols. FMP uses `FMP_API_KEY`; Alpaca uses
`ALPACA_API_KEY` and `ALPACA_API_SECRET`. A future missing credential yields
source-local `unavailable`. The manual batch is
`scripts/refresh_current_news.py`. On 2026-08-30 its reviewed hourly per-user
service/timer units were linked, enabled, and started. The timer is
active/waiting for its first normal `:10` UTC trigger; activation did not run
the service or write a store.

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
| `market:0009_fmp_daily_price_backfill` | [plan.md](../../plan.md) 7.1 FMP daily OHLCV provider evidence; no recovered migration SQL, checksum, or ordinal | Exact Stage 9 `SPY` 2026-07-01 through 2026-07-31 candidate-only non-production slice only; no general FMP backfill |
| `market:0010_stage10_market_history` | Explicit Stage 10 user authorization plus the accepted market identity, universe-snapshot, evidence, correction, and physical-lock contracts | Current S&P 500/Nasdaq-100/Dow 30 members plus the reviewed ETF/index roster and provider-returned daily OHLCV history; no Russell 2000 constituents or historical-membership claim |
| `macro:0012_stage11_bea_eia_live_history` | Explicit Stage 11 user authorization plus the accepted macro evidence, local-capture availability, correction, pagination, and physical-lock contracts | Dedicated BEA NIPA and EIA retail/weekly candidate-only history; no fixture-table reinterpretation, vintage invention, or broader macro discovery |
| `macro:0013_live_gdp_cpi_vintages` | Explicit GDP/CPI vintage authorization plus accepted immutable evidence, source-vintage availability, version, no-write replay, and physical-lock contracts | Four fixed official BEA/BLS series with bounded historical archives and current-only refresh; no Stage 11 reinterpretation or broader macro discovery |
| `macro:0014_live_employment_vintages` | Explicit employment-vintage authorization plus accepted immutable evidence, provider-alias lineage, source-vintage identity, no-write replay, and physical-lock contracts | Philadelphia Fed payroll/unemployment matrices plus exact BLS current payroll/unemployment series; no invented release dates, historical refetch, or broader macro discovery |
| `macro:0015_live_macro_history_extension` | Explicit one-time macro-history authorization plus sealed-response adoption, source-native series, provider-alias lineage, and exact registry projection | Seven fixed BLS CPI windows and four Philadelphia Fed GDP/GNP/CPI matrices; no repeat, scheduler, public consumer, or BEA-series reinterpretation |
| `macro:0016_fmp_calendar_wholesale_evidence` | Explicit `2.21.0` FMP wholesale-calendar authorization plus immutable raw-evidence, local-replay, no-write, and physical-lock contracts | One-time retained FMP calendar response/row evidence supporting local replays; no original-release archive, public consumer, scheduler, or repeat provider request |
| `news:0005_fmp_stock_latest` | Explicit bounded FMP stock-news authorization plus accepted news evidence, version, and physical-lock contracts | One private page-zero/page-limit-1000 capture only; no historical completeness, tombstone inference, public consumer, or live-success claim |
| `news:0006_fmp_stock_latest_current` | Separate forward current-feed migration, immutable local-capture availability, article-version lineage, and physical-lock contracts | Applied repeatable UTC-hour-slot capture; first bounded request retained 229 articles; no timer or public raw/body exposure |
| `news:0007_current_multi_source` | Forward current multi-source evidence/article migration, fixed feed identities, local-capture availability, and private body/raw boundary | Applied manual batch; all 20 bounded requests succeeded; no timer or public raw/body exposure |
| `news:0008_adopt_fmp_news_legacy` | Tracked pre-registry FMP table schema plus explicit inactive private ownership | Existing 22,910 rows preserved behind an exact schema guard; no collector, tool, dashboard, export, or source ID |

Except for the explicitly authorized Stage 9, Stage 10, Stage 11, official
macro-vintage, FMP wholesale-evidence, and bounded FMP stock-news rows above,
all other recovered and future migration scopes remain unallocated. The
rejected `macro:0017_bls_cpi_release_archive` candidate is not an unallocated
future scope. The exact market options ownership at `0029`/`0030` and the
filing-issuer derived-view invariants at `0031` are represented by the frozen
Stage 4 resources above, without asserting historical SQL parity.

## Activation checklist

1. Review final UTF-8/LF SQL bytes and compute lowercase SHA-256.
2. Declare the same reviewed value in `config/system_registry.json`.
3. Verify registry/resource/order integrity before opening a writer.
4. Apply Stage 1 through Stage 4 resources only to four explicit temporary
   paths during their fixture gates. The Stage 9 through Stage 11 resources are
   eligible only inside their exact authorized non-production candidate paths
   after their applicable offline gates. No allocation is generic
   live-provider authorization.
5. Run initialization, rerun, tamper, wrong-store, partial-failure, and
   source/backup/restored evidence tests.
6. During a forward-preserving table rebuild, temporarily disable foreign-key
   enforcement only inside the atomic rebuild and require `foreign_key_check`
   before the migration ledger advances.
7. Promote reconstruction state to `fixture_validated` only with the complete
   applicable stage evidence. The 30 Stage 1 through Stage 4 resources and the
   Stage 9 through Stage 11 resources are `fixture_validated` in the canonical
   registry after their applicable offline gates. None can be relabeled
   `recovered_exact`.
8. Stage 4 and Stage 9 independent verification passed. Stage 10 retained
   candidate receipts were independently re-inspected. The Stage 11 private
   candidate population and receipt are complete. Every
   future migration allocation remains serialized under the next applicable
   roadmap gate.

## FMP company research-input allocation — 2026-09-06

The user authorized the bounded FMP repair for MSFT and AAPL research inputs.
This forward-only company allocation preserves all applied company migrations.

| ID | Store | Local ordinal | Immutable resource | SHA-256 | Reconstruction state | Activation status |
| --- | --- | ---: | --- | --- | --- | --- |
| `company:0008_fmp_research_inputs` | company | 8 | `quant_data/migrations/company/0008_fmp_research_inputs.sql` | `17b35efa1ba21b3526e6b0fda361736ac6a11b79cfd7109c92ea6b75dfee2831` | `fixture_validated` | Applied at 2026-09-06T18:28:16.997079Z after full offline coverage and independent review; immutable ledger/checksum and new-table integrity verified |

The allocation owns `company_fmp_research_snapshots` and
`company_fmp_research_rows`: immutable retained-response lineage and versioned
source rows, with separate annual/quarter identities and local capture cutoffs.
It does not redefine SEC fundamentals, establish historical consensus, infer
accounting basis or currency, change other store ownership, or add a recurring
collector. The exact provider scope and request cap are recorded in the
[FMP research-input contract](FMP_RESEARCH_INPUTS_CONTRACT_2026-09-06.md).
