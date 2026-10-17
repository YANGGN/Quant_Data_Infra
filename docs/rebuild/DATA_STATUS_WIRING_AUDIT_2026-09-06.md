# Data Status wiring audit — 2026-09-06 UTC

Original read-only audit requested by the user to compare “No retained capture” with the implementation plan and actual storage. No provider request, credential use, scheduler action, data publication, or application change occurred during that audit. The subsequently requested UI correction is recorded below.

## Result

All 39 flagged entries were checked against their registry-owned relations and successful output lineage in the four canonical stores.

| Finding | Entries | Interpretation |
| --- | ---: | --- |
| Populated, successful shared ingestion output | 18 | Status reader ignores existing output links. |
| Populated official vintages with native capture ledger | 2 | Storage design is intentional; status adapter is missing. |
| Empty older fixture/candidate relations | 16 | Earlier contracts remain registered; operational successors use other relations. |
| Empty sector/industry classification dataset | 1 | Planned feature has only a fixture importer; live coverage remains unfinished. |
| Frozen original one-shot news datasets | 2 | A rejected HTTP 200 response on 2026-08-15 produced no capture/articles; current news uses separate successors. |

The storage separation is intentional. Treating every active registry dataset as an operational feed and reporting missing generic capture metadata as missing data is a reporting gap. The fixture prefix alone cannot classify lifecycle: many current live collectors deliberately reuse fixture-named datasets.

## Plan and implementation evidence

- [Stage 3](../../ROADMAP.md#7-stage-3--restore-market-and-macro-domains) and [Stage 4](../../ROADMAP.md#8-stage-4--restore-company-news-and-options) completed synthetic fixture gates. Completion did not mean that every table was populated in the operational stores.
- [Stage 9](../../ROADMAP.md#13-stage-9--bounded-fmp-non-production-backfill-candidate-only) was a separate one-symbol non-production FMP candidate. The current market path uses Stage 10/12 storage; the three market.fmp Stage 9 relations in the canonical store are empty.
- [The architecture](../../ARCHITECTURE.md#store-local-control-plane) explicitly includes ingestion_run_outputs. The coordinator writes all output IDs there (quant_data/ingestion.py:543–551), while the status reader checks only the primary dataset ID (quant_data/data_status.py:293–344).
- The official-vintage publisher writes macro_live_vintage_captures and immutable membership directly (quant_data/macro/live_vintages.py:2275–2396). The generic status reader has no native adapter for this ledger; no missing parent ingestion run should be fabricated.
- [The recovery plan](../../plan.md#security_classifications) calls for sector/industry mapping and a profile/classification refresh. The current classification registry binding is only fixture.market.catalog_import; its implementation is the offline Stage 3 catalog service. instrument_classifications is empty. This remains a live-coverage gap, rather than a failed scheduled job.
- [The news contract](FMP_NEWS_POPULATION.md) preserves the original one-shot profile and defines a separate repeatable current-feed successor. The native old outcome is response_rejected, HTTP 200, at 2026-08-15T16:26:04.829352Z. Both old capture and article relations are empty. No repeat was performed.

Some older tool versions still consume the empty compatibility relations. A populated newer feed does not imply identical behavior or full feature parity in every old tool version. This audit checks dataset wiring and retention, not a new all-tool functional certification.

## Dataset inventory

“Populated” means at least one registered relation contains rows; it does not assert complete symbol/period/field coverage. In particular, populated company expectations do not mean earnings-calendar and guidance subfamilies are populated.

| Dataset | Finding | Actual retention or successor |
| --- | --- | --- |
| `fixture.market.catalog_evidence` | Empty earlier storage | Current roster/universes use Stage 10 storage; older catalog contract is separate |
| `fixture.market.controlled_universes` | Empty earlier storage | Current roster/universes use Stage 10 storage; older catalog contract is separate |
| `fixture.market.daily_price_evidence` | Empty earlier storage | Current daily prices and identities use `market.stage10.*` |
| `fixture.market.daily_prices` | Empty earlier storage | Current daily prices and identities use `market.stage10.*` |
| `fixture.market.instrument_classifications` | Live coverage unfinished | Sector/industry rows absent; only fixture catalog importer is wired |
| `fixture.market.instruments` | Retained; shared run | `fixture.market.options` |
| `fixture.market.option_capture_evidence` | Retained; shared run | `fixture.market.options` |
| `market.alpaca.option_raw_evidence` | Retained; shared run | `fixture.market.options` |
| `market.fmp.daily_price_evidence` | Empty earlier storage | Current daily prices and identities use `market.stage10.*` |
| `market.fmp.daily_prices` | Empty earlier storage | Current daily prices and identities use `market.stage10.*` |
| `market.fmp.instruments` | Empty earlier storage | Current daily prices and identities use `market.stage10.*` |
| `market.stage10.instruments` | Retained; shared run | `market.stage10.daily_prices`, `market.stage10.universes` |
| `market.stage10.source_evidence` | Retained; shared run | `market.stage10.daily_prices`, `market.stage10.universes` |
| `fixture.macro.eia_retail` | Empty earlier storage | Current EIA uses Stage 11 histories and generic macro series |
| `fixture.macro.eia_retail_evidence` | Empty earlier storage | Current EIA uses Stage 11 histories and generic macro series |
| `fixture.macro.eia_weekly` | Empty earlier storage | Current EIA uses Stage 11 histories and generic macro series |
| `fixture.macro.eia_weekly_evidence` | Empty earlier storage | Current EIA uses Stage 11 histories and generic macro series |
| `fixture.macro.gdp_vintages` | Empty earlier storage | Current GDP uses `macro.official_vintages` |
| `fixture.macro.recession_periods` | Empty earlier storage | Current NBER monthly history uses generic macro series; old interval tool remains separate |
| `fixture.macro.rtdsm_employ_evidence` | Retained; shared run | `fixture.macro.rtdsm_employ`, `fixture.macro.treasury_yield_curves` |
| `fixture.macro.soma_evidence` | Retained; shared run | `fixture.macro.soma_summary` |
| `fixture.macro.stage3_catalog` | Retained; shared run | `fixture.macro.rtdsm_employ`, `fixture.macro.treasury_yield_curves` |
| `macro.bea.nipa_history_evidence` | Retained; shared run | `macro.bea.nipa_history` |
| `macro.eia.electricity_retail_history_evidence` | Retained; shared run | `macro.eia.electricity_retail_history` |
| `macro.eia.petroleum_weekly_stock_history_evidence` | Retained; shared run | `macro.eia.petroleum_weekly_stock_history` |
| `macro.fmp.economic_calendar_incremental_events` | Retained; shared run | `macro.fmp.economic_calendar_incremental_evidence` |
| `macro.official_vintages` | Retained; native ledger | `macro_live_vintage_captures` and its fact/membership relations |
| `macro.official_vintages_evidence` | Retained; native ledger | `macro_live_vintage_captures` and its fact/membership relations |
| `fixture.company.corporate_actions` | Retained; shared run | `fixture.company.action_evidence` |
| `fixture.company.expectations` | Retained; shared run | `fixture.company.expectation_evidence` |
| `fixture.company.filing_issuer_membership` | Retained; shared run | `fixture.company.fundamentals` |
| `fixture.company.filings` | Retained; shared run | `fixture.company.fundamentals` |
| `fixture.company.issuers` | Retained; shared run | `fixture.company.fundamentals` |
| `fixture.company.sec_evidence` | Retained; shared run | `fixture.company.fundamentals` |
| `fixture.news.evidence` | Empty earlier storage | Current news uses current-feed/multi-source successors; frozen Stage 4 tables stay empty |
| `fixture.news.items` | Empty earlier storage | Current news uses current-feed/multi-source successors; frozen Stage 4 tables stay empty |
| `fixture.news.search_index` | Empty earlier storage | Current news uses current-feed/multi-source successors; frozen Stage 4 tables stay empty |
| `news.fmp.stock_latest_articles` | Frozen old attempt | Old response rejected; current news is separately retained and polled |
| `news.fmp.stock_latest_evidence` | Frozen old attempt | Old response rejected; current news is separately retained and polled |

## Smallest corrective scope

1. Resolve successful capture/outcome evidence through explicit ingestion_run_outputs links.
2. Add a fixed native capture adapter for official vintages; retain native outcome adapters for other non-generic collectors.
3. Distinguish operational datasets from fixture-only, historical/candidate and superseded entries without deleting their evidence or relying on names.
4. Track genuinely unfinished source coverage separately, beginning with sector/industry classifications. Do not populate obsolete tables merely to remove a status badge.

These are findings, not newly executed repairs or authorization for another live workload.

## Validation and limits

The audit used the established quiet immutable readers for each canonical store, checked registry-owned relation existence/non-emptiness, and inspected successful output associations. All reader contexts closed normally with their file/sidecar stability checks. The 39 entries are all still registry-active, which explains their inclusion in the generic page. No full integrity scan, historical population, provider probe, or test suite was needed or run for this read-only investigation. Machine-readable audit metadata is at `/tmp/inspector-no-capture-audit.json`.


## Implemented UI correction

The user subsequently authorized fixing the UI from these findings. The
HTML-only reader in `quant_data/inspector_retention.py` now follows explicit
successful output links and validates official-vintage native capture membership.
`quant_data/inspector_status.py` combines this with existing source-date and fetch
receipt metadata; `quant_data/canonical_inspector.py` supplies the registry to
this fixed read-only overlay. Missing or unavailable capture evidence remains
explicit. A successful capture does not replace an independently recorded latest
attempt outcome or invent a successful-fetch timestamp.

`quant_data/dashboard/data_status_page.py` and its scoped Inspector CSS now show
matching exclusive summary/row categories, muted fixture/historical/legacy rows,
planned coverage, and capture provenance and successor IDs in record details.
All 21 displayed fields remain available in the no-JavaScript table. Existing
source dates, Eastern fetch times, expected weekly age explanations, and timer
columns remain separate. Original public status and outcome fields are retained
in details. The public `data.get_dataset_status@1.0.0` tool and JSON route are
unchanged; their generic attribution limitations still apply.

The reloaded local Inspector on port 8766 returned 62 records: **40 data retained,
0 needs attention, 1 not yet live, 21 legacy/fixtures, 0 status unknown**. The
planned entry is sector/industry classifications. These categories describe
retained evidence and reviewed lifecycle, not full source coverage or provider
health. The original JSON still reports 23 current and 39 no_data records, as
expected for its unchanged contract.

Validation completed:

- 52 focused tests passed across retention, source/receipt presentation, layout
  markup, schedules, timezone handling, and Inspector routes/API compatibility.
- After the final outcome-preservation correction, the affected 13 tests passed
  again. Shared capture evidence cannot overwrite a separately recorded outcome.
- 59 fixture browser assertions passed, including all lifecycle/evidence states,
  summary/row agreement, 21-field preservation, details and keyboard focus,
  escaping, no-JavaScript fallback, and 1440/360/320-pixel layouts. Desktop/mobile
  screenshots were visually inspected; no blockers, outside-origin requests,
  or JavaScript errors were found. The temporary server was stopped and all
  fixture store fingerprints were unchanged.
- Production HTTP smoke confirmed the counts above, shared/native attribution,
  old news outcome, weekly source explanation, Eastern fetch timestamp, and the
  unchanged read-only JSON envelope. The smoke helper initially expected a
  top-level records array; correcting it to the existing result.records envelope
  passed without an application change.
- Scoped compile/whitespace checks passed. Full offline suite was not required
  for this bounded read-only UI correction; no schema or public tool contract
  changed.

Temporary evidence: `/tmp/inspector-retention-focused-tests.log`,
`/tmp/inspector-status-retention-TO5uWeoc/browser-results.json`, its desktop/mobile
screenshots and `fingerprints.json`, and
`/tmp/inspector-retention-real-http-smoke.json`.

No provider requests, collector reruns, data populations, or recurring-unit
changes were performed for this UI correction. Existing data and historical
evidence remain intact; unfinished live classification coverage remains labelled.


## Follow-up: separate dataset tables

The user requested a dropdown separating actual live data from fixture, legacy,
and not-yet-live entries. Data Status now renders two distinct table workspaces:
`Live data` (default) and `Fixtures, legacy & planned`. Group membership uses
reviewed lifecycle only, so active failed, stale, missing, and unavailable feeds
remain in the live table. Each table has its own count and relevant summary
cards. All records and 21 fields remain present exactly once. Switching is local,
clears selected record details, and keeps keyboard focus usable. Without
JavaScript both complete tables remain readable.

Changed presentation files: `quant_data/dashboard/data_status_page.py`,
`quant_data/dashboard/static/inspector.css`, and
`quant_data/dashboard/static/inspector.js`. Existing presentation/route tests and
the tool-platform contract were updated; readers and public API were unchanged.

Validation: 47 focused tests completed successfully across runs (45 initially
passed; two old unified-table caption assertions were updated and re-passed).
41 bounded browser assertions passed, including dropdown keyboard switching,
scoped counts, record/detail preservation, active failures in the live group,
no-JavaScript, escaping, and 1440/360/320-pixel layouts. Desktop/mobile screenshots
were visually inspected with no blockers. Switching made no requests, fixture
stores were unchanged, and the verified fixture server was stopped.

The existing Inspector was reloaded on port 8766. Actual HTTP smoke confirmed
40 live records and 22 other records (21 legacy/fixture/historical and 1 planned),
with all 62 IDs unique and all 21 fields preserved. Evidence is retained in
`/tmp/inspector-table-groups-tests.log`,
`/tmp/inspector-table-groups-copy-recheck.log`,
`/tmp/inspector-status-groups-s4cze1s8/`, and
`/tmp/inspector-table-groups-real-http-smoke.json`.
