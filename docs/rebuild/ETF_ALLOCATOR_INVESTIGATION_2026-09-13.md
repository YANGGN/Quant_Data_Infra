# ETF allocator snapshot investigation — 2026-09-13 UTC

The connection works. The unusable result combines genuine missing August observations, an unconditional adjustment/readiness gate, and metadata that is partly retained elsewhere but not joined. **All 25 ETFs already have September 11 prices in the same canonical Stage 10 dataset.** No paid-tier upgrade or new infrastructure is justified by the evidence.

A complete usable snapshot is **not available through 1.0.0 as deployed**. A bounded price repair and a narrow versioned current-research implementation can use the existing FMP subscription, retained Alpaca identity evidence, publishers and calculation kernel. Historical publication and adjustment reconstruction remain unestablished.

## Subsequent September 13 implementation decision

The user accepted FMP `close` as already split-adjusted and requested that
tools use it without adjusting it again. The
[version 2 contract](../LOCAL_AGENT_TOOLS.md#etf-version-2-provider-adjusted-close)
implements that source binding. The investigation and version 1 receipts
below retain their original observation time. Their descriptions of a missing
binding refer to version 1.

Version 2 uses the existing retained prices unchanged and computes supported
features; no ETF split-history maintenance or reconstruction engine is added.
It explicitly reports mixed capture vintages. A single-response full lookback
is the simplest existing option when a common provider adjustment vintage is
required. The proposed 24/25-request repairs below remain unexecuted; this
document does not grant provider or recurring-job execution authority.

## Version 2 public verification at 04:12:01 UTC

The documented launcher was repeated with both explicit versions and the same
complete 25-symbol roster at `2026-09-13T04:12:01Z`, on registry `2.84.0`
and catalog `2.30.0`. Both calls succeeded, returned 25 rows and selected
August 31. Main/WAL/SHM/journal metadata stamps were unchanged.

| Public version | Available feature values | Symbols with all nine features | Overall status |
| --- | ---: | ---: | --- |
| 1.0.0 | 0 | 0 | not_established |
| 2.0.0 | 33 | 1 (SPY) | not_established: incomplete roster features |

Version 2 establishes the documented FMP close basis for all 25 symbols.
SPY has all nine values; each other ETF has 12-to-1-month momentum. The
remaining eight values per symbol retain explicit missing-price reasons.
`price_adjustment_applied_by_tool` is false. The partial result is useful
current research input, but it is not a complete allocation packet for all
25 ETFs. Mixed provider capture vintages and historical reconstruction
limitations remain explicit; no split adjustment or collection was performed.

The original September 5 and 02:40 UTC results below describe version 1,
before this correction. They are preserved as historical evidence.

Evidence: [current full response and receipt](../../.local/etf-snapshot-v2/v2-response.json),
[version 1 comparison](../../.local/etf-snapshot-v2/v1-response.json),
[exact request](../../.local/etf-snapshot-v2/v2-request.json),
[verification summary](../../.local/etf-snapshot-v2/verification.json),
[describe](../../.local/etf-snapshot-v2/describe-v2.json), and
[exact predecessor checks](../../.local/etf-snapshot-v2/compatibility.json).
Only registry version, version-policy declarations and the versioned schema
catalog change relative to the exact 2.83 predecessor. Existing tools,
datasets, stores, collectors, migrations and jobs are unchanged.

Validation: all 21 focused ETF/calculation tests pass
([final log](../../.local/etf-snapshot-v2/focused-tests.log)); the two
targeted generated-catalog and manifest tests also pass (23 tests total).
Current inventory is verified as 45 versioned tools, 63 successor variants
and 166 catalog contracts. Generated output check and diff check pass. Exact 2.82-to-2.83 and 2.83-to-2.84 generation,
the frozen 2.69 predecessor, unchanged default v1 and immutable-store
behavior were checked. Independent read-only reviews found no outstanding
domain or registration defect after the metadata correction.

A supplementary broad price-reader test run overlapped registry edits and
was interrupted while its project-data metadata guard was walking the
operational tree; it produced no passing suite result. It is not counted
above. An additional broad historical-projection inventory test was also
stopped as supplementary after several minutes; its directly affected
current counts, generated catalog, manifest and exact predecessor checks
were completed separately. Neither interrupted run is claimed as passing.
The full offline suite was not run: this is the accepted additive
local-tool lane, with unchanged shared store, selection and calculation
semantics. No test, shell, provider fetch or recurring-unit change remains
pending from this task.

## Actual public verification

At **2026-09-13T02:40:30Z**, I ran the documented absolute `bin/quant-data-tools` launcher: manifest, describe for explicit `portfolio.get_etf_allocator_snapshot@1.0.0`, then call with the complete requested 25-symbol roster and that current decision time.

- Registry: **2.82.0**; process exit 0; receipt outcome `succeeded`.
- 25 records, 25 lineages, no truncation; selected month-end **2026-08-31**.
- Status `not_established`; ready symbols **0**; all 225 feature cells null.
- Request ID: `604830a41509841b2ed1ffa548eecb25e1cd6406f49fca8a74119af37b4ce4c9`.
- Snapshot ID: `3b6ce5f8cd1faa2223909a8cc27a77da8b575397943813ea3dfa67f7f7d3ab39`.
- Full [request](../../.local/etf-snapshot-investigation-20260913/request.json), [response and receipt](../../.local/etf-snapshot-investigation-20260913/response.json), [manifest](../../.local/etf-snapshot-investigation-20260913/manifest.json), and [description](../../.local/etf-snapshot-investigation-20260913/describe.json) are retained.

The historical prose at the top of the operating envelope still describes registry 2.70.0. Runtime discovery and this receipt establish the actual current revision.

## What is available

| Input | Actual state | Classification / consequence |
| --- | --- | --- |
| Latest daily closes | September 11 for all 25 ETFs, in `data/market.sqlite` | Available now; no alternate database required |
| August monthly window: SPY | All 272 expected sessions from August 1, 2025 through August 31, 2026 | Complete price-date coverage; still blocked by basis gate |
| August monthly window: IWM | 270 observations; August 28 and 31 absent | Genuine gap: 2 sessions |
| August monthly window: other 23 | 261 observations; August 17–21, 24–28 and 31 absent | Genuine gap: 11 sessions each |
| Split-only price convention | FMP documents `close` as split-only; retained captures identify the full-EOD endpoint and exact field | Supporting evidence exists; not bound into snapshot semantics |
| Common adjustment vintage across captures | Provider adjustments can revise old prices; current tool does not reconcile this | Must be explicit and checked; endpoint semantics alone are not a common-vintage audit |
| ETF corporate-action histories | Zero matching action versions or action-source artifacts for the 25 symbols in the company store | Not retained; current company jobs target issuer-ready equities |
| ETF identity | Stable IDs and asset type retained for all 25 | Available; originally curated ETF identity, not historical classification reconstruction |
| Name and exchange | Hash-verified retained Alpaca asset catalogue and exact Quant mappings for all 25 | Existing evidence is disconnected from public snapshot |
| Legal structure, leverage, inverse status | No explicit fields establishing these in the audited retained sources | Additional factual evidence/mapping needed; absence of a flag is not false |
| Original publication times / historical listing and adjustment vintages | Not retained by the relevant sources | Historical reconstruction unavailable locally; not shown to be a subscription-tier problem |
| Latest job's seven HTTP 402 results | Seven index symbols, none in the requested ETF roster | Observed account/endpoint access limitation unrelated to these ETFs |
| FMP `etf/info` | Documented endpoint, no retained ETF research rows or active snapshot binding | This account's entitlement was not probed; no basis to call it either available or unavailable here |

Complete symbol coverage, instrument IDs, capture IDs, dates and request scopes are in the [immutable coverage audit](../../.local/etf-snapshot-investigation-20260913/coverage-audit.json). The table below also records the exchange label in the retained Alpaca catalogue; it is not an inferred historical exchange.

| Symbol | Latest retained date | Latest date in August feature window | Missing August-window sessions | Retained exchange |
| --- | --- | --- | ---: | --- |
| SPY | 2026-09-11 | 2026-08-31 | 0 | ARCA |
| QQQ | 2026-09-11 | 2026-08-14 | 11 | NASDAQ |
| DIA | 2026-09-11 | 2026-08-14 | 11 | ARCA |
| IWM | 2026-09-11 | 2026-08-27 | 2 | ARCA |
| VEA | 2026-09-11 | 2026-08-14 | 11 | ARCA |
| VWO | 2026-09-11 | 2026-08-14 | 11 | ARCA |
| IEF | 2026-09-11 | 2026-08-14 | 11 | NASDAQ |
| TLT | 2026-09-11 | 2026-08-14 | 11 | NASDAQ |
| TIP | 2026-09-11 | 2026-08-14 | 11 | ARCA |
| LQD | 2026-09-11 | 2026-08-14 | 11 | ARCA |
| HYG | 2026-09-11 | 2026-08-14 | 11 | ARCA |
| GLD | 2026-09-11 | 2026-08-14 | 11 | ARCA |
| SLV | 2026-09-11 | 2026-08-14 | 11 | ARCA |
| PDBC | 2026-09-11 | 2026-08-14 | 11 | NASDAQ |
| XLB | 2026-09-11 | 2026-08-14 | 11 | ARCA |
| XLC | 2026-09-11 | 2026-08-14 | 11 | ARCA |
| XLE | 2026-09-11 | 2026-08-14 | 11 | ARCA |
| XLF | 2026-09-11 | 2026-08-14 | 11 | ARCA |
| XLI | 2026-09-11 | 2026-08-14 | 11 | ARCA |
| XLK | 2026-09-11 | 2026-08-14 | 11 | ARCA |
| XLP | 2026-09-11 | 2026-08-14 | 11 | ARCA |
| XLRE | 2026-09-11 | 2026-08-14 | 11 | ARCA |
| XLU | 2026-09-11 | 2026-08-14 | 11 | ARCA |
| XLV | 2026-09-11 | 2026-08-14 | 11 | ARCA |
| XLY | 2026-09-11 | 2026-08-14 | 11 | ARCA |

## Why older endpoints appear

[etf_snapshot.py:104](../../quant_data/market/etf_snapshot.py#L104) sets the source query end to the completed month-end, not to the latest retained session. [stage10_series.py:565](../../quant_data/market/stage10_series.py#L565) selects retained versions in that date interval and then applies capture availability at the decision cutoff.

Therefore `source_last_observation_date` means the latest selected date **inside the feature window**. September prices are correctly excluded from August features. Changing the feature endpoint to September 11 would change the monthly strategy. A successor should separately expose latest retained session and feature-window endpoint.

The owning dataset is `market.stage10.daily_prices`, backed by `stage10_daily_prices`, `stage10_daily_price_versions` and `stage10_daily_price_captures`. Both alternative retained price models, `fmp_daily_prices` and `prices_daily`, have no matching roster data. See [alternate-data audit](../../.local/etf-snapshot-investigation-20260913/alternate-metadata-audit.json).

The gaps have identifiable collection history:

1. August 17–28 preceded daily-timer activation. The earlier base/extension populations ended August 14. The separately scoped IWM collection supplied prices through August 27.
2. The daily timer was activated August 29 with no catch-up. On August 31 and September 1, its v1.1 collector stopped on AVB's HTTP 200 empty response at ordinal 55. The retained journals show 55 attempts, 54 results, and **no requested ETF attempted** on either date.
3. SPY received its own September 6 repair covering August 17–September 1. That explains its different endpoint. The other 24 did not receive that repair.
4. Later runs resumed current collection but did not revisit August. The expanded route now requests only the last seven calendar days.

The earlier [FMP repair handoff](FMP_RESEARCH_INPUT_HANDOFF_2026-09-06.md#price-gap-diagnosis-and-repair-scope) records the AVB stop and later failure-isolation correction. Private dated evidence is under `data/.stage12/market-v1/stage12e-market-close/`; the [bounded journal inventory](../../.local/etf-snapshot-investigation-20260913/legacy-job-coverage.json) reconciles ETF attempts. Older `store_unavailable` batch labels are **not proof of a SQLite outage**: some represent isolated provider failures after successful publications.

## Current collection jobs

Read-only host inspection found:

- **quant-data-market-close.timer:** enabled, active/waiting; next September 14 at 18:00 America/New_York. Its September 11 service ran 22:00:03–22:26:52 UTC and exited 75, `complete_with_gaps`. It made 2,359 requests, published 2,352 successful responses (one empty), and recorded seven 402 responses. Every requested ETF was successfully refreshed. The failures were `^AXJO, ^FCHI, ^GDAXI, ^GSPTSE, ^KS11, ^NDX, ^TWII`. The job is not inactive or wholly failed.
- **quant-data-company-market-refresh.timer:** enabled, active/waiting at 19:00 Eastern. September 11's selected-action lane finished all 4,402 requests, with 4,357 successes and 45 source failures; no pending scopes. The wrapper also reports legacy estimate partial coverage. None of these jobs currently supplies ETF actions.
- **quant-data-selected-company-refresh.timer:** enabled/waiting; service inactive with no execution timestamps yet. This is an unobserved scheduled company job, not an ETF repair mechanism.

Evidence: [host status](../../.local/etf-snapshot-investigation-20260913/job-status.txt), [price result](../../data/.operations/collection/daily-prices/current/2026-09-11/result.json), [action result](../../data/.operations/collection/actions-selected/current/2026-09-11/result.json). Current seven-day behavior is in [selected_price_refresh.py](../../quant_data/operations/selected_price_refresh.py).

**No recurring job needs a manual restart to solve the August snapshot.** Its next rolling window will not reach August. Do not rerun the broad completed equity backfill or the IWM population.

## Adjustment evidence and the blanket gate

FMP's [official FAQ](https://site.financialmodelingprep.com/faqs) identifies `close` as adjusted only for splits and `adjClose` as adjusted for splits and dividends. The [full-EOD documentation](https://site.financialmodelingprep.com/developer/docs/stable/historical-price-eod-full) identifies the exact retained endpoint. FMP separately documents a [non-split-adjusted endpoint](https://site.financialmodelingprep.com/developer/docs/stable/historical-price-eod-non-split-adjusted) and a dividend-adjusted endpoint.

Retained full-EOD JSON contains `close`; the canonical parser stores it as `close_value`. No `adjClose` field is substituted and the examined response does not contain it. Thus a reviewed source binding can establish **provider-supplied split-adjusted, distribution-excluding close, as captured**. This is an evidence-backed interpretation of an identified provider field, not relabeling raw prices. It does not require applying another split factor or undoing dividends.

Current implementation cannot recognize that evidence:

- [stage10_series.py:885](../../quant_data/market/stage10_series.py#L885) hardcodes `adjustment_status=not_established`; existing public series schemas also constrain that value.
- [etf_calculations.py:352](../../quant_data/market/etf_calculations.py#L352) immediately returns nine nulls unless the caller supplies the exact split-only status.
- [etf_snapshot.py:160](../../quant_data/market/etf_snapshot.py#L160) unconditionally appends provenance/reconstruction gaps; its row defaults and summary hardcode blocked readiness, false completeness, null feature date and zero ready symbols.
- [public adapter:29](../../quant_data/tool_platform/etf_snapshot.py#L29) unconditionally emits `not_established` and both warnings.

Repairing price collection alone therefore cannot make v1 ready. Changing only the series metadata would also leave incorrect readiness diagnostics and break predecessor schema expectations.

A local dependency check, without modifying the tool or emitting usable prices, confirmed that **conditional on a reviewed compatible split-only binding**, SPY has inputs for all nine calculations. Every other ETF already has endpoints for `return_12_to_1_month`; the other eight features remain blocked by the August gaps. See [conditional coverage](../../.local/etf-snapshot-investigation-20260913/conditional-feature-coverage.json).

The genuine remaining adjustment issue is **vintage coherence**. Mixing captures from before and after a split can mix scales even when each field is split-adjusted. Verify intervening actions or acquire a coherent required lookback where that cannot be established. No missing corporate-action response is proof of no split. The existing [FMP split parser](../../quant_data/company/fmp_market_data.py#L473) retains event date and denominator-to-numerator quantities, but there are no ETF records to join. A second split adjustment to already adjusted `close` would be wrong.

## Metadata mapping

The September 11 Alpaca asset response was collected at **13:12:26.384377Z**. Its SHA-256 is `eec4c7eb6cad078917d03668c0e2819876d067bdf1b2b034eec99e0f798bd112`. All 25 exact symbol/asset-ID/pointer mappings match that body and their existing Quant IDs.

Sources: `data/.operations/news-expansion-20260911/assets-response.json`, `assets-result.json`, and `retained-alpaca-mapping.json`; mapping declarations are retained in the market collection model. [Extracted verified evidence](../../.local/etf-snapshot-investigation-20260913/retained-assets.json) contains each row and pointer.

This source supplies names, exchange, active status and provider asset IDs. Its broad `us_equity` class does not distinguish legal ETF structures. Tradability, margin requirements and absent attributes do not establish nonleveraged/noninverse status. [Alpaca's asset documentation](https://docs.alpaca.markets/us/reference/get-v2-assets-1) describes the catalogue and supported attributes.

The snapshot and `market.search_instruments@2.0.0` read only the Stage 10 identity projection, whose curated ETF names/exchanges are null. A real [public SPY instrument-search response](../../.local/etf-snapshot-investigation-20260913/public-instrument-search.json) confirms those nulls.

The smallest enrichment is a read-only join to retained identity evidence with its own capture time. Legal structure, exposure and explicit leverage/inverse classification require a small evidence-backed mapping from issuer disclosures or suitable provider facts. Keep fund-versus-share-class identity explicit. Do not infer these from the word “Trust,” from a ticker, or from the absence of a leveraged flag.

FMP's [ETF information endpoint](https://site.financialmodelingprep.com/developer/docs/stable/information) exists, but no ETF research rows are retained here and no authenticated entitlement probe was made. It is unnecessary to assume an upgrade: names/exchanges already exist, and issuer disclosures can supply factual classification. The local operating record identifies the existing plan as FMP Premium; actual recent ETF price successes establish access to the required price endpoint.

## Smallest practical fix and proposed version

**First prepare a 24-request missing-window repair** through existing `HistoryPriceWindow`, `history_price_units` and `SelectedPriceHistoryPublisher` in [collection_price_windows.py](../../quant_data/operations/collection_price_windows.py), using the existing selected-price binding, FMP credential resolver, allowance and market publisher:

| Symbols | Exact window | Requests | Missing August observations |
| --- | --- | ---: | ---: |
| The roster excluding SPY and IWM (23) | 2026-08-17 through 2026-08-31 | 23 | 253 |
| IWM | 2026-08-28 through 2026-08-31 | 1 | 2 |
| SPY | Already complete | 0 | 0 |

One attempt per request, no retry. This is a proposed finite repair, not execution authorization supplied by this investigation. Extending those same windows through September 1 would also close the next daily gap with no additional request count; those 24 extra observations are not required for August features.

Before declaring adjustment coherence, check whether the retained captures share a compatible split basis. If evidence cannot establish that, the simplest finite alternative is **one full required-lookback request per ETF, 25 total**, using the same endpoint and publisher for August 1, 2025–August 31, 2026. This bounds re-observation to the actual feature window, preserves old versions, and avoids building a corporate-action reconstruction framework. It needs an explicit repeat scope. No fetch was executed.

Publish **`portfolio.get_etf_allocator_snapshot@2.0.0`**, preserving 1.0.0 and its existing shared price-reader contracts:

- Explicit purpose/convention: **current research from retained provider knowledge available by the decision time**.
- Preserve the existing split-only, distribution-excluding mathematical definitions, exact monthly endpoint and per-feature session requirements.
- Bind source semantics by provider + endpoint + field + evidence, privately; no caller switch asserting adjustment provenance.
- Report adjustment vintage/coherence, feature observation date, latest retained session and capture times separately.
- Compute independently supported features and actual readiness counts; do not let historical-backtest certification or one missing classification suppress unrelated numeric features.
- Separate price-feature readiness, metadata completeness and historical reconstruction status. Preserve historical status as unestablished where appropriate.
- Join available identity evidence and report unsupported classifications explicitly.
- Update versioned schema/catalog/registry metadata, documented examples and focused compatibility/cutoff/missingness tests.

For today's decision, August 31 is an **observation date**. A September collection timestamp can support a September decision but cannot certify that the same value or identity was known on August 31. Original publication time remains null. A present-day provider-adjusted history is not a historically reconstructed adjustment vintage. Missing historical listing, publication and corporate-action knowledge should prevent a historical-backtest claim, not all truthful current feature calculations.

The consuming app should discover and explicitly select the successor, retain full receipts/lineage, distinguish data readiness from historical certification, and evaluate freshness using the feature date. No consumer risk limits, volatility rule, approvals or trade execution were investigated or changed.

## Validation and limits

- Public full-roster snapshot repeated successfully; representative public instrument search verified.
- Market/company reads used the existing descriptor-pinned quiet immutable gateway, including its sidecar and before/after stamp checks.
- All 25 Alpaca mappings verified against retained original bytes and digest.
- Nine existing pure offline ETF calculation tests passed; conditional feature-dependency audit completed.
- No implementation change, provider API request, credential access, scheduler mutation, canonical publication, commit, push, or consuming-app access occurred.
- Worktree already contained extensive unrelated changes; they were preserved. This investigation adds only this report and private evidence.
- Full suite and formal independent verification were not run for this read-only investigation. Account access to unqueried ETF metadata endpoints, complete legal classifications, and cross-capture split coherence remain unverified.

**Answer:** existing resources are sufficient for the proposed current-research solution; the current 1.0.0 response is not usable as a complete 25-ETF feature packet. A narrow repair and versioned evidence binding are needed, not a new platform or a blanket subscription upgrade.
