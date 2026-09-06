# Rebuild Documentation Index

Status: Accepted  
Purpose: navigation, authority, and change rules for the Quant Data
Infrastructure rebuild documents
Workflow revised: 2026-09-05 — user-approved autonomy and validation policy

Use [Section 2](#2-document-map) to locate relevant contracts; the stage and
registry narrative is historical context, not required reading for every task.
Reuse unchanged sections already read in the session. The
[operating envelope](CURRENT_OPERATING_ENVELOPE.md) is the sole index of recorded
operational authorization and dated activation evidence. Neither this index nor
a dated activation record proves current host state.

## 1. Scope

This directory converts the historical recovery specification in
[plan.md](../../plan.md) into accepted implementation contracts. Stages 1
through 5 are implemented and independently verified. The bounded Stage 6
four-route local portal was accepted on 2026-08-10 with an explicit
browser-automation waiver. The bounded offline Stage 7 manual fixture
rehearsal is implemented and independently verified. The user has authorized
the bounded offline Stage 8 JSON Atlas/export implementation. Its primary
fixture gate and independent verification passed. On 2026-08-14 the user
explicitly waived unavailable in-app browser automation and accepted the
bounded Stage 8 exit gate as complete; this is not browser-pass evidence.
Later documented behavior remains closed until separately authorized
executable evidence passes.

The user has separately authorized one bounded Stage 9 preparation: a manual
FMP daily OHLCV slice for `SPY`, inclusive `2026-07-01` through `2026-07-31`,
in `/home/volatility/quant-data-nonprod/stage9-fmp-spy-202607`. Its
primary and independent offline gates and bounded live receipt passed. It is
candidate-only and does not authorize a
scheduler, public exposure, Atlas inclusion, promotion, or store retirement.

The Stage 10 base population and eight-window historical extension are complete
private candidates in the exact isolated target. Their retained receipts have
been independently re-inspected and must not be re-requested. Stage 11 is also
complete as a private candidate in the same cohort: BEA, EIA retail, and EIA
weekly are published, its targeted checks passed, and its private completion
receipt is retained. Its provider requests must not be repeated.

On 2026-08-15 the user selected `data/market.sqlite` as the canonical
project-relative market default. Stage 12A is implemented and independently
verified as its offline authority/path gate, and Stage 12B is implemented and
independently verified as an offline fixture-only collector. The bounded
no-copy Stage 12C population is complete and independently verified under its
[two-session contract](STAGE12C_MARKET_GAP_V1.md) and immutable
[evidence record](STAGE12C_EVIDENCE.md). Registry `2.14.0`/schema `1.8.0`
binds 629 one-attempt units: 619 published complete, three successful-empty,
and seven narrowly sealed authorized HTTP 402 terminal outcomes. The Stage
12C receipt records 4,237,131 current rows, 4,237,873 immutable versions, and
5,210 captures with clean invariants; the later additive IWM result is
recorded separately below.

Stage 12D is complete and independently verified under its
[no-transfer project-local adoption/freeze contract](STAGE12D_PROJECT_LOCAL_OPERATIONALIZATION.md),
[ADR 0010](../adr/0010-stage12d-no-transfer-market-adoption.md), and immutable
[evidence record](STAGE12D_EVIDENCE.md). Exactly two canonical read-only proofs
produced distinct immutable receipts with one semantic proof and left the main,
WAL, SHM, and journal stamps unchanged. Independent reconciliation did not
reopen SQLite or compute a new full-database hash, and it is not a backup or
recovery proof. No second multi-gigabyte database, copy, move, replacement,
backup, migration, registry bump, promotion pointer, provider/network access,
public exposure, or scheduler change occurred within the Stage 12D proof.
Later Stage 12E authority and dated activation evidence are indexed in the
[operating envelope](CURRENT_OPERATING_ENVELOPE.md#recurring-exceptions).

The [2026-08-29 personal-project recovery decision](../../plan.md#22-dated-clarification-personal-project-recovery-scope)
removes formal four-store backup/restore certification from the current
restoration exit criteria. GitHub is the recovery authority for code,
migrations, tests, and configuration; database loss may be handled by
rebuilding schemas and separately authorized provider re-fetches. Existing
backup fixtures remain safety evidence, not a required certification milestone.

Registry `2.15.0`/schema `1.8.0` changed only the macro, company, and news
default basenames to `data/macro.sqlite`,
`data/company.sqlite`, and `data/news.sqlite`, matching the existing files.
No database file was copied, renamed, opened, or mutated, and the exact
historical projection restores Stage 12C/D registry `2.14.0`.

Registry `2.16.0`/schema `1.8.0` added the isolated
official GDP/CPI vintage migration, private evidence/canonical datasets, and
fixed BEA/BLS collectors. The canonical macro backfill and first refresh are
complete: 16 captures, four series, 2,136 releases, 4,012 versions, and 640
current observations passed integrity, lineage, raw-hash, duplicate, and
current-pointer checks. A non-persistent weekday 09:05 ET user timer refreshes
only current BEA/BLS inputs; the 14 historical archive requests are not
scheduled. Its exact projection restores `2.15.0`.

Registry `2.17.0`/schema `1.8.0` adds only the
isolated employment-vintage migration and private Philadelphia Fed historical
and BLS current collectors. The completed three-request backfill added 984
releases, 14,449 immutable versions, and 1,993 current observations across
payroll and unemployment, while preserving source-vintage labels without
invented release dates. A non-persistent first-Friday 10:05 ET timer fetches
only the two-series BLS current response; historical workbooks are not
scheduled. Its exact projection restores byte-exact registry `2.16.0`.

Registry `2.18.0`/schema `1.8.0` adds macro
ordinal 15 and two manual-only history collectors without a new dataset,
credential, public consumer, or scheduler. The completed one-time extension
reused five sealed responses, made six BLS requests, and brought official
all-items CPI back to `1947-01` and core CPI back to `1957-01`; Philadelphia
Fed NOUTPUT/ROUTPUT remain separate source-native GNP/GDP series. The retained
Stage 11 weekly crude cohort was adopted locally as 2,289 exact versions and
current rows with no provider request. Its exact projection restores
byte-exact registry `2.17.0`.

Registry `2.19.0`/schema `1.8.0` adds only the
manual-only `fmp.macro.gdp_cpi_release_calendar_history` collector, reusing
`fixture.macro.economic_calendar` and the existing `macro.official_vintages`
tool dependency. The completed, no-repeat FMP history covers exactly 56
contiguous windows of at most 90 days from `2013-01-01` through `2026-08-17`
and 941 normalized calendar events/versions. It adds no migration, dataset,
job, scheduler, public consumer, or surprise table; no timer was installed and
the two existing macro timers are unchanged. GDP surprise is calculated on
demand as one best-available record per quarter. It prefers an exact-date BEA
first release (`advance`, or source-native `initial`); when that consensus is
absent, it selects an exact-date `second`, then exact-date `third` release.
Every selection compares FMP consensus with the BEA actual from the same
release date and stage; FMP GDP actual is never used. Equivalent reviewed
aliases collapse only when their same-date values agree; conflicts fail
closed. Later stages are explicitly marked `is_fallback`. The current
canonical read returns all 55 quarters from `2012Q4` through `2026Q2`: 10
advance, one initial, 10 second, and 34 third; 54 have numeric surprises and
`2012Q4` is `missing_consensus`. CPI has 864 unchanged same-event results.
This derived policy adds no provider request or scheduler. Its exact
historical projection restores `2.18.0`, preserving the `2.18.0` to `2.17.0`
historical projection above.

Registry `2.20.0` adds only the reviewed
`fmp.macro.employment_release_calendar_refresh` declaration and establishes
payroll and unemployment surprise calculation from each same-reference-period
FMP actual and consensus. The reviewed bounded, non-persistent refresh timer is
installed, enabled, and active at 08:15 and 08:45 America/New_York on weekdays
under the user's explicit scheduler approval. Exact projection restores
byte-exact `2.19.0`.

The latest fully documented registry successor is `2.21.0`/schema `1.8.0`.
It adds macro
migration 0016, one private wholesale calendar evidence dataset, and one
manual-only collector. The completed one-time run retained all raw response
bytes and 42,890 rows from 56 contiguous `2013-01-01` through `2026-08-17`
windows. The original local replay made zero provider requests and normalized
323 v1 employment events/versions. Additive v2 payroll and v3 unemployment
local replays each wrote 162 reference-period-aware events and versions and now
support 324 on-demand employment surprises: 162 per series, with 321 `ok`, two
`missing_consensus`, and one `missing_actual`. Delayed releases map by explicit
reference month; unemployment `2025-10` remains `missing_actual` because FMP
supplied no actual. Identical second replays made zero requests and zero writes.
No public consumer, surprise table, job, or new timer was added, and the exact
historical projection restores byte-exact `2.20.0`.

On 2026-08-20 the user authorized a simpler derived CPI-surprise policy. FMP
calendar events supply both actual and consensus; the surprise consumer does
not query the retired BLS CPI original-release archive. Ordinary results use
one FMP event. A narrow repair may combine complementary rows only when CPI
kind, derived reference month, UTC event date, and unit match and the finite
actual and consensus values are each unique. The later event remains primary
and all source event-version lineage is retained. This repairs headline CPI
MoM for `2025-11` as `0.1 - 0.3 = -0.2`; same-side or otherwise incomplete
rows remain missing, and conflicts fail closed. The change is on-demand logic
only: it does not alter raw evidence, write a surprise table, call a provider,
migrate a store, or change a scheduler.

On 2026-08-21 the user accepted [ADR 0011: retire the proposed BLS CPI
original-release archive](../adr/0011-retire-proposed-bls-cpi-release-archive.md).
It rejects the never-active `2.22.0` candidate, including
`macro:0017_bls_cpi_release_archive`, its archive datasets and collector, and
all archive relations. The candidate is declarative working state, not evidence
of provider execution, canonical-store publication, public exposure, or
scheduler authority. The current accepted configuration is the exact
`2.21.0`/schema `1.8.0` registry: because no declarative content in that
accepted revision changes, discarding the unaccepted candidate does not create
a `2.23.0` successor. This narrow retirement does not alter the completed
`2.16.0` BLS annual-revision snapshots or its authorized current BLS refresh.


On 2026-08-24 the user accepted
[ADR 0012: compact future FMP calendar retention](../adr/0012-compact-fmp-calendar-retention.md).
Registry `2.40.0` keeps every migration-0016 response and row immutable and
readable, while future material polls append one small receipt and only changed
raw event versions, then replace one singleton latest-response cache. Exact
semantic replay remains a total no-write. The established request bound,
credential resolver, lock, and timer cadence do not change; this implementation
does not itself authorize or perform a provider request or canonical-store
migration.

On 2026-08-24 the user explicitly requested the first raw market-data tool.
Registry `2.41.0` adds `market.get_price_series` as a local read-only
adapter over the verified Stage 10 reader. It returns open, high, low, and
close typed series for one ticker, with independently optional inclusive start
and end dates. The active manifest has 58 names; the frozen recovered
compatibility target remains 57. No provider, credential, write, scheduler,
export, hosting, or deployment scope was added.

On 2026-08-24 the user requested a local interface for agents in other
projects on the same computer plus ticker discovery. Registry `2.42.0` adds
`market.get_available_ticker` over the current retrievable Stage 10 price
universe and advances the active manifest to 59 names while preserving the
frozen 57-name compatibility target. The fixed
[local-agent guide](../LOCAL_AGENT_TOOLS.md) documents a no-URL strict-JSON
subprocess with host-owned registry and store routing. This adds no direct
SQLite access, provider request, credential, write, migration, scheduler,
export, hosting, or deployment scope.

On 2026-08-25 the user selected canonical access as Step 1 of the remaining
tool foundation. Registry `2.46.0` adds native Stage 10 volume and macro
release-calendar readers plus explicit v2 macro search, describe, and series
variants. Stored current pointers, availability cutoffs, and evidenced
first-release flags define the selection modes; official-vintage IDs never
fall back to the generic macro core. The active manifest has 61 names and
projects exactly to registry `2.45.0`. This adds no portfolio or position
tool, provider request, credential, canonical write, migration, scheduler,
export, hosting, or deployment scope.

On 2026-08-26 the user selected Steps 2-4 of that foundation. Registry
`2.47.0` adds explicit `2.0.0` quality-audit and time-series-transform
successors plus four native statistics tools: distribution diagnostics,
covariance/correlation matrices, explicitly seeded deterministic bootstrap
confidence intervals, and deterministic PCA. The tools consume supplied typed
Stage 10 trailing-return series and open no store. The active manifest has 65
names, catalog `2.10.0` has 52 contracts, and the exact predecessor projection
is registry `2.46.0` with catalog `2.9.0`. No portfolio or position semantics,
provider request, credential, canonical write, migration, scheduler, export,
hosting, or deployment scope was added.

Later on 2026-08-26 the user selected the proposed technical-indicator
sequence. Registry `2.48.0` adds an explicit store-free
`market.technical_indicators@2.0.0` successor while preserving the
reconstructed v1 behavior. It consumes unmodified typed Stage 10 OHLCV
series, calculates one of 15 deterministic indicator specifications per
call, and returns one through three aligned scalar series with explicit
warm-up/undefined missingness and derived lineage. The active manifest
remains 65 names, with 15 version policies and 19 variants; catalog `2.11.0`
has 54 contracts. Exact projection restores byte-identical registry `2.47.0`
and catalog `2.10.0`. This adds no provider request, credential,
canonical-store operation, migration, dataset, collector, write path,
scheduler, export, hosting, deployment, portfolio, or position scope.

On 2026-08-26 the user requested broad option-surface coverage. Registry
`2.49.0` adds one private, manual-only Alpaca collector over the fixed ETF
universe `SPY`, `QQQ`, `IWM`, `DIA`, and the eleven sector ETFs, with exact
DTE targets `1`, `2`, `3`, `7`, `14`, `30`, `60`, `90`, `180`, and `365`.
Nearest positive listed expiries are selected deterministically and duplicate
target mappings are captured once. The fixed local Inspector adds a read-only
`options-surfaces` view. At registry `2.49.0`, the existing scheduled SPY
collector and its `spy-options` view remained SPY-only, while the broad
collector had no host scheduler or registry job. On 2026-08-27 the one
authorized manual attempt issued 17 requests and failed closed for all 15
underlyings before any option chain or publication; captures, contracts,
surfaces, and writes were zero, and the attempt was not retried. See the
[additive IWM evidence](STAGE10_IWM_EXTENSION_EVIDENCE.md). Exact projection
restores byte-identical registry `2.48.0`; catalog `2.11.0` is unchanged.
On 2026-08-30 the user separately authorized updating the enabled,
legacy-named timer in place to invoke the fixed 15-ETF grid at its then-current
15:55 weekday cadence. On 2026-08-31 the user authorized moving that same fixed
timer to 16:20 America/New_York so collection begins after the latest ordinary
ETF-option session. The reviewed 362-request, 256 MiB, 900-second bounds,
16-minute host timeout, no retry, and no catch-up are unchanged. Its registry
declaration remains `manual_only`; the host unit is the explicit recurring
exception and the 18:00 Stage 12E FMP job remains separate.

On 2026-08-27 the user clarified that IWM belongs in the Stage 10 ETF
universe even though the historical single-name rule remains the overlap of
the S&P 500, Nasdaq-100, and Dow 30 cohorts. Registry `2.50.0` adds one
private, manual-only FMP IWM full-history collector over the four existing
Stage 10 evidence, instrument, universe, and daily-price datasets. It derives
one 96-member `curated_etfs` successor from the exact frozen 95-member
snapshot plus IWM; the sealed 629-instrument receipts and their historical
IWM exclusion are not rewritten. One run is fixed to one request, one
attempt, 30,000 rows, 16 MiB, and 45 seconds. The operation reserves private
attempt state before provider access and has no registry job or timer. The
one authorized live request completed on 2026-08-27 with 1,254 daily rows and
an exact 96-member successor. Immutable postchecks returned integrity `ok`,
zero foreign-key violations, exact current/version lineage, unchanged frozen
Stage 12C counts, and 1,254 read-only Inspector-visible IWM prices. Its private
completion state blocks another invocation. See the
[additive IWM evidence](STAGE10_IWM_EXTENSION_EVIDENCE.md). Exact projection
restores byte-identical
registry `2.49.0`; no migration, dataset, tool, dashboard, export, or
scheduler was added.
Registry `2.51.0` adds only the private, manual-only fixed IWM
missing-interval collector declaration and grants no live-run authority.
Registry `2.52.0` adds seven explicit store-free analytical successors over
compatible caller-supplied Stage 10 return series. Registry `2.53.0` adds
`company.search_filings@2.0.0` with exact-CIK, cutoff-bound cursor
pagination. Registry `2.54.0` adds
`market.search_instruments@2.0.0` for retained Stage 10 FMP identity search
and `company.get_share_count_history@2.0.0` for reviewed SEC share facts.
Registry `2.55.0` adds only the explicit, store-free
`market.technical_indicators@2.1.0` successor and its causal
`supertrend_ai` calculation. Catalog `2.15.0` has 76 contracts and 30
explicit variants. Registry `2.56.0` adds only the explicit, store-free
`market.technical_indicators@2.2.0` successor and its causal
`swing_structure_forecast` calculation. At that revision the active manifest
had 65 logical names with every frozen default and predecessor unchanged;
catalog `2.16.0` had 78 contracts and 31 explicit variants. Its registry
SHA-256 was
`9782e77c530251401e9b5e42b33115949ce8bb28753f0a0471fa2e8353dd8802`,
and its catalog SHA-256 was
`cb1c6965582b90eaeec27054eda0d66e7a4c603aefa69f9b453ae342d779302b`.
Exact predecessor projection removes only v2.2 and restores byte-identical
registry `2.55.0`; its predecessor removes only v2.1 and restores
byte-identical registry `2.54.0`. The older projection chain remains intact.
These increments add no migration, dataset ownership, provider authority,
canonical write, scheduler, export, hosting, or deployment scope.


Registry `2.57.0` adds fifteen explicit read-only investment-analysis v2
successors while retaining the same 65 logical names and every frozen v1
default. The batch provides query-bound release-calendar pagination,
explicit-ticker cross-sectional performance, revision/surprise readers,
direct rates/liquidity/credit/regime views, raw research state, retained
energy readers, and normalized company fundamentals. The tools are bounded,
source-native readers or direct documented calculations; they add no
portfolio/position semantics, opaque composite score, classifier, or
investment recommendation. Catalog `2.17.0` has 108 contracts, 40 version
policies, and 46 variants, at SHA-256
`5c0ea96b9aba9d73f8f89aea20a052c858691e10a1047f22594f027d8f893101`.
The registry source SHA-256 is
`1d36ddd20494cfc0ae9e6172f662c5dfe04b905319c0f83b439446f44fd29b5e`.
Exact projection returns to registry `2.56.0`
and catalog `2.16.0`; no migration, dataset ownership, provider authority,
canonical write, scheduler, export, hosting, or deployment scope is added.

Registry `2.58.0` adds only the store-free
`market.technical_indicators@2.3.0` KDJ successor over caller-supplied typed
high, low, and close series. Catalog `2.18.0` has 110 contracts, 40 version
policies, and 47 variants at SHA-256
`4803071d5cb2ababa2710ac4c0e3eb1e1640b06aa0b6071e14c2f40be68fec72`;
the registry SHA-256 is
`02a4aeb34632c5ae194622b7de77ec31145135bdf399620e45af461132134d43`.
Exact projection removes only v2.3 and restores byte-identical registry
`2.57.0` and catalog `2.17.0`. It adds no provider request, canonical write,
migration, scheduler, export, hosting, or deployment scope.
Registry `2.59.0` keeps the same 65 logical names and adds five explicit
successors: the existing store-free Williams Vix Fix v2.4 calculation,
market breadth/risk analytics v2.1, fixed-lag seasonality for both retained
energy readers v2.1, and normalized company ratios v2.1. It also corrects the
stationarity and declared structural-break adapters to exclude only expected
incomplete return edges while continuing to reject interior gaps. Catalog
`2.19.0` has 120 contracts, 40 version policies, and 52 variants at SHA-256
`e4fdb9d9b6783ec131838762d8e2ae4b1c6833cc37c0292e1ceed09e0d919474`;
the registry SHA-256 is
`0457910706181d7f4d9bb07efbf18845dd86335999b55c5d5a8d203041d315a1`.
Exact projection removes only the five new variants and restores byte-identical
registry `2.58.0` and catalog `2.18.0`. It adds no provider request, canonical
write, migration, scheduler, export, hosting, or deployment scope.

Registry `2.60.0` keeps the same 65 logical names and adds only the store-free
`market.technical_indicators@2.5.0` WaveTrend-with-crosses successor over
caller-supplied typed high, low, and close series. It applies the Pine channel
and average lengths, fixed `0.015` channel scaling, and fixed four-bar signal
SMA, and returns WaveTrend, signal, difference, and signed cross components;
chart levels, colors, markers, fills, and bar colors are excluded presentation
behavior. Catalog `2.20.0` has 122 contracts, 40 version policies, and 53
variants at SHA-256
`cebc1ca58257fed167db36973be1ce37e6e01162c2bc072c24591a673b0aedb4`;
the registry SHA-256 is
`563c4294c47d534754014dc77a4a1b39e84749cc4d8165636bafd82feff679b0`.
Exact projection removes only v2.5 and restores byte-identical registry
`2.59.0` and catalog `2.19.0`. It adds no provider request, canonical write,
migration, scheduler, export, hosting, or deployment scope.

Registry `2.61.0` keeps the same 65 logical names and adds only the store-free
`market.technical_indicators@2.6.0` Parabolic SAR successor over
caller-supplied typed high, low, and close series. It returns one aligned
price-valued stop series with explicit first-bar missingness and excludes
chart-only presentation behavior. Catalog `2.21.0` has 124 contracts, 40
version policies, and 54 variants at SHA-256
`b70798594de6149b7710b36d3b735b3d24ff81eb23277ba54dfdc697aceb0efc`;
the registry SHA-256 is
`0f43045c1dcc46b0f9d5ecb0aaf98aa3e9ad61a43e250afc389408602a8615ea`.
Exact projection removes only v2.6 and restores byte-identical registry
`2.60.0` and catalog `2.20.0`. It adds no provider request, canonical write,
migration, scheduler, export, hosting, or deployment scope. The
[2026-08-28 current public-tool actual-data audit](CURRENT_TOOL_ACTUAL_DATA_AUDIT_2026-08-28.md)
is point-in-time execution evidence; the generated manifest and each selected
`describe` result remain the consumer contract authority.

Registry `2.62.0` keeps the same 65 logical names and adds only the store-free
`market.technical_indicators@2.7.0` rolling-regression-line successor over one
caller-supplied typed close series. Its caller-supplied integer `window` is 2
through 10,000; it fits OLS over the complete trailing window including the
current bar and emits the fitted right-edge value. Warm-up is explicit
`insufficient_history`; a null close in the lookback is explicit
`rolling_window_input_missing`, with recovery as soon as that gap leaves the
window. Catalog `2.22.0` has 126 contracts, 40 version policies, and 55
variants at SHA-256
`18e86daf6ef3291407ab794d7f53015c80bb90c88f2518891f7b904002f515a1`; the
registry SHA-256 is
`59db17edd70d8ae9aa77f1468cf7c459338e3d1dff0015b3c99853b2e1e12fd2`.
Exact projection removes only v2.7 and restores byte-identical registry
`2.61.0` and catalog `2.21.0`. It adds no provider request, canonical write,
migration, scheduler, export, hosting, or deployment scope.

Registry `2.63.0` keeps 65 logical names and adds the separate repeatable
current FMP news successor: migration `news:0006_fmp_stock_latest_current`,
two private datasets, manual-only collector `fmp.news.stock_latest_current`,
and `news.search@2.0.0`. Catalog `2.23.0` has 128 contracts at SHA-256
`05cfbfb29b544594a3b176daeca659470f3c91a20a423c730d8da9622c8cae2d`;
registry SHA-256 is
`06466e9b79be5bc0fab927a81b5972059bbad34ba4a674c1c456c3eaeaf04d72`.
Exact projection restores byte-identical `2.62.0`/`2.22.0`. The bounded
2026-08-30 proof applied migration 0006 and retained 229 articles in one
request without retry.

Registry `2.64.0` retains 65 logical names and adds
`news:0007_current_multi_source`, two active private datasets, manual-only
collector `news.current_multi_source`, and `news.search@2.1.0`. Forward
migration `news:0008_adopt_fmp_news_legacy` owns `fmp_news_articles` as an
inactive private evidence dataset behind an exact schema guard; it has no
collector, tool, dashboard, export, or source ID. Catalog `2.24.0` is
SHA-256 `6a4f7e8ce223658617512928b860f5cf5bde85e01f075070771fa019e882ed46`;
registry SHA-256 is
`b47b6ad63ecaa41477388af033c7f928083ceb5e7db17bf76ff4ab99f71f3dc4`.
The 2026-08-30 16:00 UTC manual proof completed all 20 fixed requests without
retry; all eight steps succeeded and retained 1,069 generic-source articles.
Raw evidence and bodies remain private. On 2026-08-30 the reviewed per-user
timer was linked, enabled, and started; that check recorded active/waiting for
its first normal `:10` UTC trigger, and activation did not run the service or write a
store. Exact projection removes both 2.64 migrations, all three 2.64 datasets,
its collector, and v2.1, restoring byte-identical
`2.63.0`/`2.23.0`.

Registry `2.65.0` adds market migration `market:0011_option_raw_evidence`
and a private raw-response dataset while leaving catalog `2.24.0` unchanged.
Registry `2.66.0` then adds cursor-paginated `news.search@2.2.0` and eight
bounded local read-only news/research names. Catalog `2.25.0` has 148
contracts at SHA-256
`e35b136e3e47a6211a85d62c75baf6ecd52b9246938a30549ace5c19e0c39700`;
registry SHA-256 is
`f7b8c402ce4abce5d024f7fcdc8debde97e25324739f037ef209312bb4d070f3`.
The tools expose metadata and declared deterministic derivations, never raw
responses or article bodies. Exact projection restores byte-identical
registry `2.65.0` and catalog `2.24.0`.

Registry `2.67.0` adds retained-only `data.get_dataset_status@1.0.0` and
typed v2 successors for the three canonical options-access names. Catalog
`2.26.0` has 156 contracts at SHA-256
`fcfb29de2c2138995918e40c603704a0b2df4c17b6c3229312734bb46b0f2a28`;
registry SHA-256 is
`a80b0e06db95968c9fd49cd3d90054b709c57895993a28b512ba2550e162f325`.
Data Status is explicitly retained control-plane freshness rather than live
provider/scheduler health. Options v2 keeps one capture cohort per surface,
preserves explicit missing/excluded states, and exposes no raw response bytes.
Exact projection restores byte-identical registry `2.66.0` and catalog
`2.25.0`.

For any provider, credential, scheduler, canonical-store, migration, promotion,
retirement, deployment, public-exposure, or destructive task, read the
[current operating envelope](CURRENT_OPERATING_ENVELOPE.md) before the
specialized contract. The envelope consolidates current boundaries but grants
no new authority.

Two user decisions narrow the work:

- The lost Git history will not be searched for or reconstructed. A fresh
  baseline can be created when implementation begins.
- A dedicated provider-rights governance subsystem is out of scope.

Stages 4 through 7 were implemented sequentially with offline synthetic
fixtures. Stages 4 and 5 are independently verified, Stage 6 is accepted with
its explicit waiver, and Stage 7 is independently verified. All eight
Stage 7 jobs remain disabled and manual-fixture-only. Stage 8 is a deliberate
forward reconstruction of one manual, fixture-only JSON snapshot; it is not a
claim of recovered Atlas source/package parity or historical 13-dataset
coverage.

## 2. Document map

### Direction and sequencing

- [Recovery evidence and historical specification](../../plan.md)
- [Target architecture](../../ARCHITECTURE.md)
- [Implementation roadmap](../../ROADMAP.md)
- [Current operating envelope - mandatory operational routing](CURRENT_OPERATING_ENVELOPE.md)
- [Fast path development - default workflow](FAST_PATH_DEVELOPMENT.md)
- [Parallel execution and agent cadence - exception workflow](PARALLEL_EXECUTION.md)
- [Frozen pre-compression agent instruction ledger](AGENT_INSTRUCTION_LEDGER_2026-08-20.md)
- [Migration reconstruction map](MIGRATION_RECONSTRUCTION.md)
- [Stage 1 acceptance evidence](STAGE1_EVIDENCE.md)
- [Stage 2 acceptance evidence](STAGE2_EVIDENCE.md)
- [Stage 3 acceptance evidence](STAGE3_EVIDENCE.md)
- [Stage 4 acceptance evidence](STAGE4_EVIDENCE.md)
- [Stage 5 acceptance evidence](STAGE5_EVIDENCE.md)
- [Stage 6 acceptance evidence](STAGE6_EVIDENCE.md)
- [Stage 7 acceptance evidence](STAGE7_EVIDENCE.md)
- [Stage 8 evidence record](STAGE8_EVIDENCE.md)
- [Stage 9 evidence record](STAGE9_EVIDENCE.md)
- [Stage 10 evidence record](STAGE10_EVIDENCE.md)
- [Stage 11 evidence record](STAGE11_EVIDENCE.md)
- [Stage 12 Market v1 contract](STAGE12_MARKET_V1.md)
- [Stage 12 evidence record](STAGE12_EVIDENCE.md)
- [Stage 12B incremental Market v1 collector contract](STAGE12B_INCREMENTAL_MARKET_V1.md)
- [Stage 12B evidence record](STAGE12B_EVIDENCE.md)
- [Stage 12C Market v1 two-session gap contract](STAGE12C_MARKET_GAP_V1.md)
- [Stage 12C evidence record](STAGE12C_EVIDENCE.md)
- [Stage 12D no-transfer adoption/freeze contract](STAGE12D_PROJECT_LOCAL_OPERATIONALIZATION.md)
- [Stage 12D evidence record](STAGE12D_EVIDENCE.md)

### Detailed contracts

- [Single system registry](SYSTEM_REGISTRY_SPEC.md)
- [Data and time contracts](DATA_AND_TIME_CONTRACTS.md)
- [First vertical slice](VERTICAL_SLICE_SPEC.md)
- [Composable tool platform](TOOL_PLATFORM_SPEC.md)
- [UI visual direction](UI_VISUAL_DIRECTION.md)
- [Scheduling and physical-store locking](SCHEDULING_AND_LOCKING.md)
- [Derived analytical exports](ANALYTICAL_EXPORTS.md)
- [Test strategy and acceptance](TEST_STRATEGY.md)
- [Stage 12 Market v1 operationalization](STAGE12_MARKET_V1.md)
- [Stage 12B incremental Market v1 collector](STAGE12B_INCREMENTAL_MARKET_V1.md)
- [Stage 12C Market v1 two-session gap completion](STAGE12C_MARKET_GAP_V1.md)
- [Stage 12D project-local no-transfer operationalization](STAGE12D_PROJECT_LOCAL_OPERATIONALIZATION.md)

### Architecture decisions

- [ADR-0001: Four operational SQLite stores](../adr/0001-four-operational-sqlite-stores.md)
- [ADR-0002: Single machine-readable registry](../adr/0002-single-machine-readable-registry.md)
- [ADR-0003: Three-layer data architecture](../adr/0003-three-layer-data-architecture.md)
- [ADR-0004: Point-in-time availability model](../adr/0004-point-in-time-availability-model.md)
- [ADR-0005: Composable tool core](../adr/0005-composable-tool-core.md)
- [ADR-0006: Lock by physical store](../adr/0006-lock-by-physical-store.md)
- [ADR-0007: SQLite authority and derived Parquet](../adr/0007-sqlite-authority-derived-parquet.md)
- [ADR-0008: Fresh store-local reconstruction migrations](../adr/0008-fresh-store-local-reconstruction-migrations.md)
- [ADR-0009: Canonical market operational path](../adr/0009-canonical-market-operational-path.md)
- [ADR-0010: Stage 12D no-transfer market adoption](../adr/0010-stage12d-no-transfer-market-adoption.md)
- [ADR-0011: Retire proposed BLS CPI original-release archive](../adr/0011-retire-proposed-bls-cpi-release-archive.md)

## 3. Authority and conflict handling

Before executable artifacts exist, use this order:

1. Explicit user decisions.
2. Accepted ADRs.
3. `ARCHITECTURE.md` and the focused contract specifications.
4. `ROADMAP.md` for sequence and gates.
5. `plan.md` for recovery evidence and historical context.

Within `plan.md`, Sections 18–20 supersede earlier sections where they
conflict. A remembered historical behavior is not automatically a target
decision.

After implementation begins, validated runtime behavior, immutable applied
migrations, and executable schemas become evidence. Resolve clearly superseded
guidance under the authority order and briefly record the reason; no approval
loop is needed for clear precedence. If authority or semantics remain
unresolved, stop only the affected action and continue independent work.
Never silently rewrite historical evidence or an applied migration.

The user-approved 2026-09-05 workflow is defined by [AGENTS.md](../../AGENTS.md),
the [fast path](FAST_PATH_DEVELOPMENT.md), and the validation matrix in
[TEST_STRATEGY.md](TEST_STRATEGY.md#4-test-layers). It replaces older generic
every-change/full-suite and mandatory-wave defaults. Specific accepted
milestone, release, and promotion gates retain their own requirements.

## 4. Status vocabulary

- `Proposed`: drafted but not approved for implementation.
- `Accepted`: approved as the current target contract.
- `Superseded`: replaced by a linked decision or specification.
- `Authorized`: explicit executable scope is allowed, but its exit evidence is
  not yet complete.
- `Implemented`: supported by executable evidence and acceptance results.
- `Rejected`: considered and intentionally not adopted.

The current planning package is `Accepted`. Stages 1 through 5 are
implemented and independently verified. The bounded Stage 6 portal is accepted
with its browser-automation waiver. The bounded offline Stage 7 manual fixture
rehearsal passed its primary gate and independent SolUltra verification. Stage
8 is `Implemented` and accepted for its bounded offline JSON Atlas/export
profile after its primary fixture gate and independent review passed. On
2026-08-14 the user explicitly waived the unavailable in-app
browser-automation check. The waiver closes the bounded exit gate without
claiming that unrecorded browser checks passed.

Stage 9 is `Bounded live population independently verified` for its exact FMP
non-production slice only. Promotion and retirement remain excluded.
The retained Stage 10 base and extension populations are complete,
independently re-inspected private candidates; the missing point-in-time
pre-live gate record is disclosed rather than reconstructed. The exact Stage 11
population is complete as a private, non-production candidate. This does not
authorize promotion, public exposure, scheduling, or another provider request.

Stage 12A is `Implemented and independently verified` for the bounded offline
authority/path gate, and Stage 12B is `Implemented and independently
verified — offline fixture-only`. Stage 12C is `Implemented and independently
verified` as the exact bounded no-copy two-session population under its
[contract](STAGE12C_MARKET_GAP_V1.md) and
[evidence record](STAGE12C_EVIDENCE.md). Its final receipt is bound to registry
`2.14.0`/schema `1.8.0`; the evidence discloses the sealed HTTP 402
exception and the final `mode=ro&immutable=1` source-neutrality check without
claiming a database mutation from the prior `-shm` timestamp effect.
Stage 12D is `Implemented and independently verified — no-transfer read-only
adoption/freeze` under its
[contract](STAGE12D_PROJECT_LOCAL_OPERATIONALIZATION.md) and
[evidence record](STAGE12D_EVIDENCE.md). The evidence states the post-proof
no-reopen/no-new-full-hash limitation and makes no backup or recovery claim.
It has no transfer, migration, provider, credential, public-consumer, or
scheduler authority. Subsequent Stage 12E decisions and activation evidence are
indexed in the [operating envelope](CURRENT_OPERATING_ENVELOPE.md#recurring-exceptions);
they do not change the historical scope of Stage 12D.

## 5. Shared terminology

- **Evidence layer:** immutable provider response, controlled input, request
  metadata, capture time, content identity, and ingestion receipt.
- **Canonical layer:** normalized facts with stable identity, explicit version
  or current-state policy, availability, and evidence lineage.
- **Research layer:** derived features, panels, statistics, backtests, and
  exports tied to exact canonical inputs and code/configuration versions.
- **Effective time:** when a fact applies in the source domain.
- **Available time:** earliest defensible point when the system or researcher
  could know the fact.
- **Captured time:** when this system obtained the evidence.
- **As-of cutoff:** latest allowed availability boundary for a query.
- **Operational store:** authoritative SQLite database receiving canonical
  writes.
- **Derived export:** disposable, reproducible analytical representation such
  as Parquet or an Atlas snapshot.
- **Physical lock identity:** canonical identity derived from the resolved
  database file, independent of logical job or domain name.

## 6. Change workflow

1. Identify the affected contract and ADRs.
2. Update the decision first if architecture or semantics change.
3. Update the focused specification and its acceptance criteria.
4. Update the roadmap only if sequencing or gates change.
5. Preserve recovery evidence in `plan.md`; add a dated clarification rather
   than rewriting remembered history.
6. Check all relative links and terminology.

## 7. Planning-package acceptance

This documentation package is ready for implementation review when:

- every linked document exists and has an explicit status;
- no specification claims unimplemented behavior is present;
- store ownership and layer terminology are consistent;
- point-in-time and physical-lock contracts agree across documents;
- the vertical slice has deterministic offline acceptance criteria;
- the roadmap contains no live-data step before its prerequisites; and
- all open questions are assigned to a stage rather than hidden in prose.
