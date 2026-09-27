# Tier 1: compact ETF option detail with broad-chain research summaries

> Expansion update: the user subsequently authorized the other 14 Tier-1 ETFs
> with concurrent downloads. The [expansion receipt](THETA_ETF_EXPANSION_2026-09-24.md)
> owns that finite scope, migration and execution evidence. SPY is complete and
> excluded from new requests. The original planning checkpoint below is historical.


> Implementation update: the later user instruction approved compact source
> disposal and SPY-first implementation/fetch. [ADR 0013](../adr/0013-optional-options-store-and-compact-retention.md)
> and the [implementation receipt](THETA_SPY_IMPLEMENTATION_2026-09-24.md)
> supersede the planning-only/retention restrictions below for this exact scope.
> Other ETFs and the broad stock universe remain future work.


Date: 2026-09-24
Status: Planning update; sparse sampling accepted in principle, additions proposed.
No new provider request, canonical-store access or scheduler change occurred.

## 1. User direction

The primary use is volatility signals and stock/ETF ranking. The user accepted
the compact daily strike-grid direction, then requested consideration of
round-number strikes, actual option activity and aggregate statistics for
contracts omitted from detailed storage.

Proposed Tier 1 universe:
SPY, QQQ, IWM, DIA, XLB, XLC, XLE, XLF, XLI, XLK, XLP, XLRE, XLU, XLV, XLY.

Keep the maximum available daily history and existing ten expiry targets.
Retain the eleven fixed strike/spot targets:
**80, 90, 95, 97.5, 99, 100, 101, 102.5, 105, 110, 120 percent**.

Add up to four distinct strike levels per selected expiry for round numbers
and activity. Also retain bounded daily summaries of all observed eligible
contracts, including those outside the detailed sample. The detailed contract
bound rises from 220 to 300 per ETF/session. These are proposed rules, not
implemented collector behavior or authority for a live backfill.

Use the dedicated options.sqlite design in the
[original storage plan](THETA_OPTIONS_STORAGE_AND_EXECUTION_PLAN_2026-09-24.md).
The compact aggregate-input retention policy below is a proposed scoped
change to that plan, not an existing deletion policy.

## 2. Current Alpaca behavior, verified in code

| Dimension | Current setting |
| --- | --- |
| Universe | The 15 ETFs above |
| Target time to expiry | 1, 2, 3, 7, 14, 30, 60, 90, 180, 365 calendar days |
| Actual expiry selection | Nearest eligible positive listed expiry per target; ties go to the earlier expiry |
| Repeated selections | Targets selecting the same expiry share one capture |
| Catalogue window | 1–730 calendar days; this supports nearest-target selection, not acquisition of every expiry |
| Strike band | 0.80 <= strike / underlying spot <= 1.20 |
| Calls/puts | Both; a candidate expiry needs standard call and put listings |
| Underlying reference | IEX snapshot midpoint, with the implemented positive-trade fallback |
| Option feed/account | Alpaca indicative / paper |
| Timing | Weekdays 16:20 America/New_York; OPRA trading-day gate |
| Storage | Existing market.sqlite capture/evidence tables |
| Bound | At most 362 requests, 256 MiB, 900 seconds; no automatic retries/catch-up |

The installed timer was observed active and enabled. Its installed service
invokes quant_data.operations.alpaca_etf_options_refresh.
This verifies routing/configuration, not success or complete data for every day.

Source:
- [Constants](../../quant_data/market/alpaca_options.py#L57)
- [Expiry selection](../../quant_data/market/alpaca_options.py#L2004)
- [Request construction](../../quant_data/market/alpaca_options.py#L2948)
- [Operational contract](SCHEDULING_AND_LOCKING.md#authorized-in-place-alpaca-etf-option-surface-refresh)

### Implications

- DTE targets are not exact maturities and not historical lookback windows.
  There may be fewer than ten distinct captured expirations.
- There is no tolerance enforcing closeness to a target. A 365-day target may
  select an expiry above 365 days; 730 days is a catalogue-search bound.
- Same-day expiration (0DTE) is excluded by the positive-DTE rule.
- The 80–120% band is spot moneyness, not delta. It covers different economic
  tail risks at different volatility levels and maturities.
- This is current-snapshot collection, not historical backfill.
- Quotes can be missing/stale even when a capture is structurally complete.
  Open interest and close observations have separate dated states.
- Nonstandard deliverables are excluded analytically, with raw evidence kept.
  The September 22 stale-chain fix is preserved.
- The indicative feed is not an executable quote feed. Theta observations must
  remain separately labelled and not overwrite or silently extend Alpaca series.

## 3. Detailed contract selection

### 3.1 Stable anchors and bounded additions

| Layer, per selected expiry | Maximum distinct strike levels | Selection |
| --- | --- | --- |
| Fixed grid | 11 | Nearest listed standard strike to each target percentage |
| Round-number additions | 2 | Nearby additional listed strikes at multiples of 5 or 10 dollars |
| Activity additions | 2 | One remaining volume-leading strike and one remaining OI-leading strike |
| Combined maximum | 15 | Both calls and puts; deduplicate across every selection reason |

Expiry targets remain 1, 2, 3, 7, 14, 30, 60, 90, 180 and 365 calendar days.
Select the nearest eligible actual expiry per target, tie earlier, and merge
duplicate expirations. Preserve target and actual DTE; define permissible
expiry/strike gaps before implementation. The fixed grid is not replaced by
popular strikes, so the comparable volatility series keeps stable selection
semantics. Invalid/missing anchor quotes remain explicitly missing.

Round additions use actual listed dollar strikes, not rounding option premiums
or inventing a contract. Prefer a strike on each side of spot within the
80–120% band; minimize distance within each side and prefer a multiple of 10 dollars
on an otherwise equal tie. A level already in the grid consumes no extra slot.
Freeze deterministic tie/fallback rules in the selection version. Do not keep
every multiple of 5 dollars in the band.

Roundness is an empirical research hypothesis, not proof of liquidity.
Use disjoint flags: multiple of 10 dollars; multiple of 5 but not 10 dollars; other.
Record these against the original as-traded strike and known deliverable.
Do not apply a modern split-adjusted strike to historical roundness labels.

For the two activity slots, rank all remaining resolved standard strikes of
that selected expiry, allowing strikes outside the percentage band:
- first, greatest positive combined call/put session volume;
- second, greatest positive combined call/put OI from the latest report
  available at the selection cutoff, excluding already selected strikes.

Rank with explicit metric-coverage states: do not treat an absent side as zero
or silently compare a partial sum with a complete sum. Record the constituent
call/put values and report dates. Break valid metric ties using quote quality,
relative spread, distance from spot, then strike. Leave a slot empty when no
eligible positive metric exists. Volume and OI identify activity; spread,
two-sided quote sizes and staleness separately describe liquidity.

Detailed maximum: 15 strikes x 10 expirations x 2 rights = 300 contracts per
ETF/session. Duplicates and sparse history generally reduce actual counts.
No automatic increase is allowed when the sample captures little activity;
publish its coverage and revisit the policy explicitly.

### 3.2 Point-in-time and comparability rules

Select from same-day information available at the declared EOD cutoff, using
a contemporaneous split-consistent underlying reference. Current-day volume
can select an after-close research observation, not a trade supposedly placed
earlier that day. A same-day trading strategy must use an earlier selection
manifest. Never use future OI, future listings or current liquidity for old dates.

Store selection reason(s), ranks, target/actual moneyness, actual DTE, reference
price/time, input report times, method version and quality flags. Mark arrivals
and departures from the activity sample. Derive comparable IV time series from
fixed anchors; do not splice changing activity leaders into a fixed-strike series.

Keep bid/ask and sizes, OHLC, volume, trade count, available IV/Greeks and dated
OI for detailed selections. Pin Greek methodology and preserve unknown inputs.
Do not add full-chain Greek acquisition merely to support activity summaries.

The detailed sample excludes 0DTE initially. The broad-chain summaries include
observed same-day expirations, unselected maturities and strikes outside the
grid, including above one year. Thus summary and detailed coverage differ by
design. Expired-day EOD quotes/IV still need quality exclusions.

## 4. Research summaries for the rest of the chain

### 4.1 Populations and compact dimensions

Define the active analysis universe as resolved standard contracts listed and
not expired before analysis session D, across all maturities. Source rows for
already-expired contracts and unresolved identities have separate accounting.
Bucket DTE and moneyness against that session and its underlying reference.
OI still carries its own earlier effective date; this is an active-universe,
as-known-at-cutoff view, not an assertion that OI describes positions formed on D.

For every ETF/session retain three explicitly labelled populations:
1. all eligible observed chain contracts;
2. contracts retained in detail;
3. the remaining contracts (population 1 minus population 2).

Use the same identities, acquisition revision and field-specific coverage for
each reconciliation. For additive known-value counts, full = detail + remainder.
Missing fields have separate counts. A lower-bound sum over known observations
must not masquerade as a complete total. Compute non-additive statistics within
each population; do not subtract medians, ratios or concentration indices.

Initially materialize one-way summaries, split by calls/puts:
- overall;
- DTE buckets: 0, 1–7, 8–30, 31–90, 91–180, 181–365, above 365;
- strike/spot buckets: below 80%, 80–90%, 90–95%, 95–99%, 99–101%,
  101–105%, 105–110%, 110–120%, at least 120%.

Use half-open moneyness intervals, lower-inclusive and upper-exclusive; the
final interval is open above. Preserve unknown-DTE/moneyness and unresolved
identity counts separately; they do not disappear from acquisition accounting.
This is 34 populated-or-empty logical cells per population, at most 102 per
ETF/session before explicit unknown categories. Do not create the full
DTE x moneyness x roundness Cartesian cube initially. Known empty and unknown
coverage remain distinct.

### 4.2 Recommended first-release statistics

| Statistic family | Retained measurements | Research use |
| --- | --- | --- |
| Activity and put/call balance | Call/put volume, trade counts, OI, observed/traded contract counts; ratios with denominators | Market participation, hedging-demand proxies, unusual activity |
| Maturity distribution | Volume/OI and shares across DTE buckets, explicitly including 0DTE | Short-term speculation versus longer-term positioning |
| Tail and near-ATM activity | Volume/OI by moneyness and right; out-of-money call/put shares | Downside protection, upside speculation, shifting tail interest |
| Concentration and leading contracts | Top-five contract IDs/expiry/strike/value/share by volume and OI, by right, for full and remainder; top-five shares and HHI | Dominant strikes, crowded expirations and activity missed by the grid |
| Round-number clustering | Volume/OI, eligible contract counts and shares for disjoint multiples-of-10 / multiples-of-5-only / other categories | Test whether round strikes attract activity beyond their listing prevalence |
| Quote liquidity and quality | Valid two-sided quote count, median/p90 relative spread, bid/ask size summaries, zero-bid/crossed/missing counts and available staleness indicators | Trading-cost proxies, unreliable surfaces, differences between activity and executable liquidity |
| Sample coverage | Fraction of known volume/OI captured by detailed contracts, omitted contract counts, endpoint/catalogue completion and metric coverage | Quantify what sampling loses and when a policy needs review |

Define relative spread as (ask - bid) / midpoint only for valid non-crossed
two-sided quotes with positive midpoint. These are report-time quote statistics,
not intraday liquidity or average execution cost. Do not infer quote age from
last_trade time; retain a not-observable state when quote timestamps are absent.
Specify quantile weighting/interpolation and sample counts in the method version.

Leader lists contain minimal identifying values and ranks, not full extra
contract histories/Greeks. At most 5 leaders x 2 metrics x 2 rights x 2
populations = 40 references per ETF/session; identities can repeat across lists.
HHI is sum of squared contract shares within the stated metric/population.
Record its denominator and covered count. It describes concentration, not a
prediction that the underlying must pin at a strike.

For roundness, preserve counts as well as activity shares: a large raw share
can reflect how many round strikes are listed. Causal or matched comparisons
need controls for maturity, moneyness and listing rules; the compact one-way
tables alone cannot supply every joint comparison. Core observations and
leader identities support targeted follow-up studies.

Derive trailing 20-day unusual-volume measures and longer-history percentiles
from prior-only daily aggregates. OI changes must compare compatible effective
dates and disclose expiration/listing/population changes; an OI decline is not
automatically closing flow or negative sentiment. A zero denominator yields
explicit missingness rather than a fabricated ratio.

Do not claim signed money flow, actual traded premium from volume times EOD
midpoint, dealer gamma exposure or whole-chain IV from these inputs. Initial
broad acquisition contains EOD prices/volume and OI, not every contract's Greeks.

### 4.3 Time, completeness and schema shape

[Theta EOD](https://thetadata.net/docs/operations_python/option_history_eod.html)
reports are generated around 17:15 ET and include volume, trade counts and
report-time NBBO fields.
[Theta OI](https://thetadata.net/docs/operations_python/option_history_open_interest.html)
normally reports around 06:30 ET and refers to previous-session positions.
Keep volume session, OI effective date, report/availability time and local
capture time separately. An EOD day-D signal must not use OI first reported
on D+1; a revised day-D position view has the later availability boundary.

All-expiry/all-strike requests can supply broad EOD/OI inputs. Verify actual
account coverage, completed streams and catalogue reconciliation; response
success alone does not prove a complete chain. Incomplete partitions produce
partial-labelled summaries, not complete zeros. A missing OI report is not zero.

Proposed logical families in options.sqlite:
- selection manifests with capped detailed members and reasons;
- detailed option observations and their evidence;
- versioned daily summary cells with population, bucket, metric coverage,
  selection revision, source-batch identity and aggregation version;
- bounded leader references and acquisition/retention receipts.

These are design names, not applied schema. Keep provider evidence, canonical
observations and derived summaries as distinct registered layers. Corrected
inputs produce new summary versions, preserving old cutoffs and results.
Exact semantic replay causes no canonical change.

## 5. Acquisition and evidence-retention trade-off

The earlier narrow-fetch recommendation is superseded for EOD/OI because both
global activity ranking and remainder summaries need broader observations.
Acquire all listed expirations/strikes for EOD and OI in bounded daily batches;
compute summaries and selection once from that batch. Fetch detailed Greeks
only for the selected sample. Avoid duplicate EOD/OI requests unless needed
for exact selected-evidence retention or a separately authorized correction.

The [API strike selector](https://thetadata.net/docs/operations_python/option_history_greeks_eod.html)
does not directly express a sparse percentage list. Measure selected-Greeks
batching and all-stream response sizes before setting execution tranches.
Disk savings do not remove network/processing work.

Proposed compact retention mode:
- Keep selected-detail evidence, aggregates, leader lists and their manifests
  permanently under versioned policies.
- Stage broader EOD/OI inputs until validation, reconciliation and backup have
  succeeded; propose a bounded 30-day review window before any approved cleanup.
- Retain redacted request scopes, original response digests/byte counts,
  completion receipts, source/selection/aggregation versions and coverage.
- Identify selected extracts as extracts. Preserve exact independently framed
  source messages where available; a filtered JSON object is not the original
  response. If exact selected evidence needs separate requests or retaining a
  larger source object, account for that in the pilot and storage forecast.

After broader inputs expire, stored summaries cannot support arbitrary new
historical statistics or a full source-level rebuild. Digests prove identity
against available bytes; they do not reconstruct discarded data or prove an
aggregate was calculated correctly. Corrections may require an authorized
refetch, which can return a newer provider history. Keep aggregate revisions
and mark source replayability explicitly.

This compact mode needs a specific options-summary retention decision before
implementation. It would narrow the
[ADR 0003 evidence-retention rule](../adr/0003-three-layer-data-architecture.md)
that retention cannot discard evidence needed by active facts or published
receipts, and must be reconciled with the
[data/evidence contract](DATA_AND_TIME_CONTRACTS.md#3-immutable-evidence-and-semantic-identity).
The user requested this design consideration; no existing raw evidence is
authorized for deletion by this plan. Preserve all pilot inputs pending that
decision. No cleanup timer or automatic removal is introduced.

An evidence-preserving alternative is to keep all broad EOD/OI responses
compressed. It supports full replay but adds a chain-size-dependent archive;
the compact storage estimate below must not be presented as that mode's total.
The original plan's permanent raw-retention default remains in force until a
scoped replacement policy is adopted.

## 6. Revised storage estimate

Use the existing illustrative 700–1,100 bytes per selected contract-day for
EOD/OI/Greeks indexed facts plus selected response data. This is not a measured
production benchmark for the new sampling/retention design.

| Detailed policy, all 15 ETFs | Maximum contracts per ETF/day | 10 years | 14 years |
| --- | --- | --- | --- |
| Fixed grid only | 220 | 5.82–9.15 GB | 8.15–12.81 GB |
| Fixed grid + round/activity additions | 300 | 7.94–12.47 GB | 11.11–17.46 GB |

Formula: 15 x 252 trading sessions x years x maximum contracts x bytes/row.
Numbers are decimal GB; actual history and deduplication can lower row counts.

For illustration, 102 summary cells per ETF/day over fourteen years produce
5,397,840 rows. At an assumed 300–600 bytes per indexed summary row this is
1.62–3.24 GB before leader lists, unknown categories and richer metric metadata.
Provision **2–5 GB for summaries/leader metadata**, to be measured in the pilot.
This estimate is independent of the number of unselected contracts scanned,
but not of the number of retained dimensions or versions.

The conditional compact corpus is therefore roughly **13–23 GB** over fourteen
years. Use **20–35 GB as a provisional compact working allocation**, with backup
and broad-input staging separately budgeted. This is not a validated ceiling.
Retaining all broad raw evidence adds potentially substantial storage and
invalidates that total. Measure raw compressed bytes, staging peaks, source
framing, request overhead, revisions and contract metadata before backfill.

## 7. Revised execution sequence and validation

1. Freeze the selection, cutoff, population, metric and retention contracts.
   The exact additive rules remain proposed. Do not change existing Alpaca
   behavior or its evidence retention.
2. Pilot SPY and XLK on two explicit historical sessions each, with three
   detailed expiry targets (7/30/180) but broad EOD/OI inputs for all expiries.
   Nominal unsplit work: 8 broad requests plus up to 180 selected-Greeks units
   (2 ETFs x 2 dates x 3 expiries x 15 strikes, both rights).
   Proposed cap remains 500 total data requests, one hour, 512 MiB retained
   response data, no retries. Include all catalogue, entitlement, partitions
   and any exact-detail refetches in that cap; reduce pilot scope if needed.
   Freeze actual dates/contracts before execution. No pilot is run by this plan.
3. Measure storage, request efficiency, quote/Greek quality, round/volume/OI
   overlap, extra detail counts, retained-volume/OI shares and broad-chain
   coverage. Preserve all pilot inputs; no automatic expiration.
4. Implement the registered options store and focused offline invariants:
   deduplication and 300-contract cap; no future volume/OI selection; 0/-only
   disjointness; partial/missing reports; population reconciliation; zero
   denominators; non-additive stats; source loss/replayability labels; correction
   versioning and exact replay. Apply the existing schema/core review gates.
5. Backfill four broad-market ETFs, then eleven sectors, in explicit finite
   batches with bounded temporary inputs. Do not automatically broaden a weak
   sample or retry incomplete units. Retain evidence according to the adopted
   policy and report replayability.
6. Release detailed fixed-anchor signals plus separately labelled activity and
   full/remainder summaries. Validate coverage and observed storage before
   authorizing any ongoing Theta refresh or cleanup policy.

## 8. Current status

Only the two planning documents were updated. This revision incorporates the
user's requests for round-number/liquidity-aware sampling and valuable aggregate
research statistics. Arithmetic and local document links were checked.
No provider calls, canonical SQLite access, code/unit changes, migration,
backfill, deletion or scheduler activation occurred.

The compact grid direction is accepted in principle. Exact addon rules,
retention trade-offs, source completeness and measured storage remain to be
settled before execution. Later tiers remain deferred.
