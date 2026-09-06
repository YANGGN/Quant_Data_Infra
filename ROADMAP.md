# Quant Data Infrastructure Rebuild Roadmap

Status: Accepted
Planning baseline: 2026-08-09
Scope: accepted implementation sequencing; Stages 1 through 5 are
independently verified; the bounded Stage 6 four-route portal was accepted on
2026-08-10 with an explicit browser-automation waiver; the bounded offline
Stage 7 manual fixture rehearsal passed its primary gate and independent
SolUltra verification on 2026-08-11; the bounded Stage 8 exit gate was accepted
on 2026-08-14 after primary and independent offline verification, with an
explicit waiver for unavailable in-app browser automation; the bounded Stage 9
FMP slice passed primary and independent offline verification and live-receipt
verification; the retained Stage 10 base and historical-extension candidates
are complete and independently re-inspected; the bounded Stage 11 population
and private candidate receipt are complete, with promotion and public exposure
still closed; the bounded Stage 12A authority/path gate and Stage 12B
fixture-only collector are implemented and independently verified; Stages 12C
and 12D are complete and independently verified as bounded no-copy/no-transfer
results; Stage 12E authority and dated activation evidence are indexed in the
[current operating envelope](docs/rebuild/CURRENT_OPERATING_ENVELOPE.md).
Recorded activation is not a fresh check of current host state.

On 2026-08-29 the user removed formal four-store backup/restore
certification from the restoration exit criteria for this personal project.
Version-controlled code and migrations plus documented provider re-fetching
are the accepted recovery approach; no separate recovery certification stage
is required.

## 1. Purpose

This roadmap translates the historical recovery evidence in [plan.md](plan.md)
into bounded implementation stages. It is intentionally narrower than the full
recovered feature inventory: the rebuild must prove its data contracts and
operational boundaries before restoring breadth.

The target architecture is defined in [ARCHITECTURE.md](ARCHITECTURE.md). Detailed
contracts and decisions are indexed in
[docs/rebuild/README.md](docs/rebuild/README.md).

## 2. Decisions and scope boundaries

The following decisions apply to every stage:

- Do not search for or reconstruct the lost Git history. When implementation
  begins, create a fresh repository baseline from the accepted planning
  documents.
- Stage 0 is complete. Stages 1 through 5 are fixture-validated and
  independently verified. The bounded offline Stage 6 four-route local portal
  passed its executable checks and was explicitly accepted with browser
  automation waived. The bounded offline Stage 7 manual fixture rehearsal has
  passed its primary gate and independent SolUltra verification. The bounded
  Stage 8 JSON Atlas/export profile passed its primary offline fixture gate and
  independent verification. On 2026-08-14 the user explicitly waived the
  unavailable in-app browser-automation check and accepted the bounded exit
  gate as complete. The waiver does not claim the unrecorded browser checks
  passed.
- The user has authorized only one Stage 9 live-provider preparation:
  FMP daily OHLCV for `SPY`, inclusive `2026-07-01` through `2026-07-31`,
  under `/home/volatility/quant-data-nonprod/stage9-fmp-spy-202607`. It is
  manual-only, one request, no retry, and candidate-only. The Stage 9 primary
  and independent offline gates and bounded live receipt passed. No operational
  promotion or old-store retirement is authorized.
- The retained Stage 10 population is complete for its frozen 629-symbol roster
  and eight-window historical extension. It remains private, non-production,
  and candidate-only. Its provider requests must not be repeated.
- Stage 11 completed only the exact BEA/EIA macro scope in the same isolated
  cohort. Its BEA, EIA retail, and EIA weekly phases, targeted checks, and
  private candidate receipt are complete. No provider request may be repeated,
  and no promotion or public exposure is authorized.
- A dedicated provider-rights governance subsystem is outside this rebuild
  plan. Provider access and retention choices remain explicit implementation
  inputs rather than a new platform feature.
- On 2026-08-15 the user selected `data/market.sqlite` as the canonical
  project-local market default. Stage 12A is complete and independently
  verified as the authority/path gate; Stage 12B is complete and independently
  verified as an offline fixture-only collector. The bounded no-copy Stage 12C
  population is complete and independently verified; its immutable
  [evidence record](docs/rebuild/STAGE12C_EVIDENCE.md) binds registry
  `2.14.0`/schema `1.8.0`, its frozen plan, and final receipt. It made no
  second full database copy and closes 629 one-attempt units as 619 published,
  three successful-empty, and seven sealed authorized HTTP 402 terminal
  outcomes. Stage 12D is complete and independently verified under the
  [no-transfer adoption/freeze contract](docs/rebuild/STAGE12D_PROJECT_LOCAL_OPERATIONALIZATION.md),
  [immutable evidence record](docs/rebuild/STAGE12D_EVIDENCE.md), and
  [ADR 0010](docs/adr/0010-stage12d-no-transfer-market-adoption.md). Exactly
  two read-only proofs produced distinct immutable receipts with one semantic
  proof and exact target/sidecar neutrality. Independent reconciliation did
  not reopen SQLite or compute a new full-database hash. No copy, transfer,
  backup, migration, registry change, provider/network access, public-consumer
  exposure, or scheduler action occurred. Stage 12E was separately authorized
  on 2026-08-29; its bounded daily-refresh status is recorded below and in the
  current operating envelope.
- Registry `2.15.0` aligned the remaining current store defaults with the
  existing `data/macro.sqlite`, `data/company.sqlite`, and `data/news.sqlite`
  files. It performs no database operation, and its exact historical
  projection restores `2.14.0` plus the prior `_data` declarations before
  Stage 12C/D evidence is reproduced.
- Registry `2.16.0` adds the isolated official GDP/CPI vintage model. The
  canonical macro backfill is complete with 16 captures, four series, 2,136
  releases, 4,012 versions, and 640 current observations; fixed integrity and
  lineage checks passed. The non-persistent weekday 09:05 ET user timer is
  active and refreshes only current BEA/BLS inputs. The 14 historical BLS
  archive requests are complete and excluded from recurring execution.
- Registry `2.17.0` adds the isolated employment-vintage extension. The
  completed three-request backfill retained Philadelphia Fed RTDSM payroll and
  unemployment matrices plus one BLS current capture. It added 984 releases,
  14,449 versions, and 1,993 current observations with clean hashes, lineage,
  corrections, and pointers. The non-persistent first-Friday 10:05 ET timer is
  active and makes only the one two-series BLS current request; historical
  workbooks are never scheduled or repeated.
- Registry `2.18.0` adds the one-time macro-history extension at ordinal 15
  and two manual-only collectors. The completed run reused five sealed source
  responses, made exactly six BLS requests, and added 14,577 versions plus
  1,980 current observations. A separate local-only adoption copied the sealed
  2,289-row Stage 11 weekly crude cohort without a provider request. No new
  timer, public consumer, credential, or dataset was added; exact projection
  restores byte-exact `2.17.0`.
- Registry `2.19.0` adds only the manual-only
  `fmp.macro.gdp_cpi_release_calendar_history` collector, reusing
  `fixture.macro.economic_calendar` and the existing
  `macro.official_vintages` tool dependency. The completed, no-repeat manual
  history covers exactly 56 contiguous windows of at most 90 days from
  `2013-01-01` through `2026-08-17`, with 941 normalized FMP calendar
  events/versions. It adds no migration, dataset, job, scheduler, or surprise
  table; no timer was installed, the two existing macro timers are unchanged,
  and its exact historical projection restores `2.18.0`. On demand, GDP uses
  one best-available record per quarter: an exact-date BEA first release
  (`advance`, or source-native `initial`), otherwise an exact-date `second`,
  then an exact-date `third`. FMP consensus is always compared with the BEA
  actual from that same release date and stage; FMP GDP actual is never used.
  Equivalent reviewed aliases collapse only when their same-date values agree,
  and conflicts fail closed. Later stages are explicitly marked `is_fallback`.
  The current canonical read returns all 55 quarters from `2012Q4` through
  `2026Q2`: 10 advance, one initial, 10 second, and 34 third; 54 have numeric
  surprises and `2012Q4` is `missing_consensus`. CPI has 864 unchanged
  same-event results. This derived policy adds no provider request or
  scheduler.
- Registry `2.20.0` adds only the reviewed
  `fmp.macro.employment_release_calendar_refresh` declaration and establishes
  payroll and unemployment surprise calculation from each same-reference-
  period FMP actual and consensus. Its
  bounded, non-persistent incremental refresh timer is
  installed, enabled, and active at 08:15 and 08:45 America/New_York on
  weekdays under the user's explicit scheduler approval. Exact projection
  restores byte-exact `2.19.0`.
- Registry `2.21.0` adds macro migration 0016, one private wholesale FMP
  calendar evidence dataset, and one manual-only collector. The completed
  one-time run retained all 42,890 rows and raw response bytes from 56
  contiguous 2013-01-01 through 2026-08-17 windows. The original zero-network
  replay normalized 323 v1 employment events/versions. Additive v2 payroll and
  v3 unemployment replays each wrote 162 reference-period-aware events and
  versions. They now expose 324 on-demand surprises: 162 per series, with 321
  `ok`, two `missing_consensus`, and one `missing_actual`. Integrity, hashes,
  membership, and run/artifact/snapshot lineage are clean; identical second
  replays wrote nothing. No provider request, public consumer, surprise table,
  job, or new timer was added, and exact projection restores byte-exact
  `2.20.0`.
- On 2026-08-21, [ADR 0011](docs/adr/0011-retire-proposed-bls-cpi-release-archive.md)
  rejected the never-active `2.22.0` BLS CPI original-release archive candidate.
  It never entered the active lineage; CPI surprises remain FMP-only, and this
  does not alter the completed `2.16.0` BLS annual-revision work.
- Registries `2.23.0` through `2.26.0` add only manual collectors for the FMP
  Treasury curve, New York Fed headline rates and repo facilities, and the
  aggregate SOMA summary. Their bounded canonical populations are complete and
  must not be repeated without a new explicit scope.
- Registry `2.27.0` adds only three credential-free, manual collectors using
  existing generic macro tables: Federal Reserve H.4.1 total assets, reserve
  balances, and TGA; Chicago Fed NFCI and ANFCI; and BIS U.S. private-sector
  credit-to-GDP, credit gap, and debt-service ratio. Their one-, one-, and
  two-request canonical populations completed on 2026-08-22, passed immutable
  read-only integrity checks, and are exposed by three fixed Inspector views.
  No migration, dataset, credential, scheduler, public route, or export was
  added.
- Registry `2.28.0` adds only the credential-free, manual NY Fed CMDI
  collector using the same generic macro tables. Its single-request canonical
  population completed on 2026-08-22 with 1,125 weekly rows each for the
  overall, investment-grade, and high-yield indexes, and the fixed
  `Corporate bond distress` Inspector view is live. No migration, dataset,
  credential, scheduler, public route, or export was added.
- Registry `2.29.0` adds only three manual collectors using existing generic
  macro tables: Treasury Fiscal Data daily TGA closing balance, EIA Lower-48
  weekly working natural-gas storage, and a monthly 0/1 recession indicator
  derived at capture time from the fixed NBER business-cycle chronology. The
  EIA route reuses the existing credential resolver; the other two are
  credential-free. Their three one-request canonical populations completed on
  2026-08-22 with 1,089 Treasury rows (`2022-04-18` through `2026-08-20`),
  868 EIA rows (`2010-01-01` through `2026-08-14`), and 2,061 derived NBER
  monthly rows (`1854-12` through `2026-08`). Immutable integrity and
  foreign-key checks passed, and all three fixed local Inspector views return
  the stored rows. No migration, dataset, scheduler, public route, export, or
  new credential mechanism was added, and exact projection restores
  byte-exact `2.28.0`.
- Registry `2.30.0` adds only one credential-free, manual BLS collector for
  `WPSFD4` PPI Final Demand, `CES0500000003` Average Hourly Earnings, and
  `PRS85006092` quarterly Nonfarm Business Labor Productivity. Its initial
  2017-2026 POST retained 268 observations. Five later authorized,
  non-overlapping productivity-only requests extended `PRS85006092` through
  1947-Q2 and added 199 observations. An earlier mixed 1997-2006 request was
  rejected before publication. A later authorized three-attempt,
  no-retry extension rejected PPI-only 2007-2016 before publication, then
  added 182 PPI/earnings observations from 2009-2016 and 34 earnings
  observations from 2006-2008. The current source-native checkpoint is 683:
  201 PPI from `2009-11`, 245 earnings from `2006-03`, and 237 productivity
  from `1947-Q2`, all through their previously recorded current endpoints.
  Immutable integrity, foreign-key, sidecar, and Inspector checks passed, and
  the successful windows must not be repeated. No migration, dataset,
  credential, scheduler, public route, or export was added, and exact
  projection restores byte-exact `2.29.0`.
- Registry `2.31.0` adds additive macro migration 0017 for real and nominal
  GDI in the existing BEA vintage relations. The same implementation increment
  expands the existing one-response NY Fed and Chicago Fed collectors with
  SOFR distribution/volume/index/averages and NFCI risk/credit/leverage, and
  exposes the established EIA monthly electricity-retail relations in the
  fixed local Inspector. No new provider family, credential mechanism,
  dataset, collector, public route, or export is introduced.
- A later EIA petroleum fast-path extension reuses the existing EIA
  credential resolver, collector binding, generic macro publisher, canonical
  store, and fixed local Inspector. Three separate one-request sources retain
  weekly U.S. total-motor-gasoline stocks, distillate-fuel-oil stocks, and
  finished-motor-gasoline product supplied. Their complete historical
  populations contain 1,911, 2,290, and 1,854 current observations
  respectively through `2026-08-14`, with no schema, migration, registry,
  dataset, provider-family, credential-mechanism, or public-route change.
- A later official-conditions fast-path extension adds BLS Employment Cost
  Index (`CIS1010000000000I`), headline Chicago Fed CFNAI, and FRED
  `INDPRO` through existing credential-free provider paths and the generic
  macro publisher. Their authorized canonical historical populations retain
  102 quarterly ECI observations from `2001-Q1` through `2026-Q2`, 710
  monthly CFNAI observations from `1967-03` through `2026-04`, and 1,291
  monthly industrial-production observations from `1919-01` through
  `2026-07`. The fixed Inspector returns all three. The same user decision
  expands the established weekday aggregate to twenty-three operations and a
  request cap of 31: ECI rides its existing BLS request, while CFNAI and
  `INDPRO` add one request each. No migration, registry revision, credential
  mechanism, new unit, cadence change, public route, or export is introduced.
- Registry `2.32.0`, schema `1.9.0`, adds explicit semantic-version selection
  under the existing `market.get_returns` and `market.get_forward_returns`
  names. Omitted or explicit v1 remains the frozen `not_established` contract;
  explicit v2 uses the existing Stage 10 read-only price model for typed
  close-to-close trailing or forward returns. The v1 catalog remains
  byte-identical, the v2 contracts live in a separate generated catalog, and
  exact projection restores registry `2.31.0`. This is a local tool-platform
  change only: no provider operation, credential, canonical-store write,
  scheduler, export, hosting, or deployment is added.
- Registry `2.33.0` adds explicit `2.0.0` variants of
  `timeseries.describe`, `timeseries.align`, and `timeseries.correlation`.
  They consume already-extracted Stage 10 v2 return-series contracts and do
  not open a store. The shared layer validates return, point-in-time, unit,
  frequency, and registry-revision compatibility; reapplies the cutoff to each
  local-capture observation; preserves explicit missingness and indexed input
  lineage; rejects truncated correlation samples; and fails rather than
  silently truncating an over-limit result. The stricter composition input
  schema does not alter the frozen return-v2 output contract. Omitted or
  explicit v1 selection remains unchanged. Exact projection restores
  byte-exact registry `2.32.0`.
  This is an offline composability increment only: no database read or write,
  provider, credential, scheduler, export, hosting, or deployment is added.
- Registry `2.34.0` adds explicit `2.0.0` variants of
  `econometrics.regression`, `econometrics.rolling_regression`, and
  `econometrics.stationarity`. They accept compatible, non-truncated trailing
  Stage 10 v2 return series and perform no store access. OLS and rolling OLS
  expose classical homoskedastic inference with explicit 95% confidence and
  numerical-backend metadata; rolling windows are fixed and contiguous.
  Stationarity is a constant-only, caller-selected fixed-lag ADF test with
  MacKinnon 2010 finite-sample critical values, explicit decisions at 1%, 5%,
  and 10%, and no approximate p-value. Frozen independent reference vectors
  and hostile-input tests cover each kernel. Omitted or explicit v1 selection
  remains unchanged; exact projection restores byte-exact registry `2.33.0`.
  This adds no database read or write, provider, credential, scheduler, export,
  hosting, or deployment. Phillips-Perron, autolag, robust covariance, and
  predictive or causal claims remain outside this increment.
- Registry `2.35.0` adds one private BEA NIPA `T20600` collector for personal
  income, disposable personal income, and personal consumption expenditures.
  One fixed `Year=ALL` request supplies all three monthly series through the
  existing BEA credential resolver and generic macro publisher. It adds no
  migration, dataset, provider family, credential mechanism, or public
  consumer, and exact projection restores byte-exact registry `2.34.0`.
- Registry `2.36.0` adds explicit `2.1.0` variants of
  `econometrics.regression` and `econometrics.rolling_regression`. The
  variants add HC1, HC3, and fixed-lag Bartlett Newey-West inference with
  asymptotic-normal coefficient tests, plus fixed-lag Ljung-Box,
  Koenker-Breusch-Pagan, and Jarque-Bera residual diagnostics. Frozen
  independent NumPy/SciPy vectors, hostile-boundary tests, rolling-versus-
  standalone equivalence, and direct/HTTP parity validate the new semantics.
  Existing `2.0.0`, omitted-v1, and frozen-v1 behavior remains unchanged;
  exact projection restores byte-exact registry `2.35.0`. This is an offline,
  caller-supplied, store-free increment with no provider, credential,
  canonical-store access, migration, scheduler, export, hosting, or deployment.
- Registry `2.37.0` adds an explicit `2.1.0`
  `econometrics.stationarity` variant alongside unchanged ADF-only `2.0.0`.
  It uses a caller-fixed Bartlett lag for level KPSS, the published 1992
  critical-value table without an approximate p-value, and an explicit joint
  ADF/KPSS interpretation. Frozen independent vectors, hostile boundaries,
  direct/HTTP parity, and exact projection to `2.36.0` validate the increment.
- Registry `2.38.0` adds the `2.0.0`
  `econometrics.structural_breaks` policy. It runs pooled, pre-break, and
  post-break classical OLS at one caller-declared zero-based first post-break
  row and reports the Chow F statistic, F-tail probability, three fit
  summaries, and coefficient comparison matrix. It performs no break search,
  multiple-testing adjustment, or multiple-break inference. Independent
  break/no-break vectors, failure boundaries, direct/HTTP parity, and exact
  projection to `2.37.0` validate the increment. Exact finite-sample F
  inference requires Gaussian, homoskedastic, independent errors and the
  standard exogenous fixed-design linear-model conditions. Catalog `2.5.0`
  contains
  24 contracts; the frozen v1 catalog is unchanged. Both revisions are
  caller-supplied, store-free increments with no provider, credential,
  migration, scheduler, export, hosting, or deployment.
- Registry `2.39.0` adds an explicit `3.0.0` variant of
  `econometrics.regression` with three selected modes: directional
  Engle-Granger cointegration, fixed-order constant VAR, and conditional
  directional Granger-causality inference. Inputs are compatible caller-
  supplied horizon-one trailing Stage 10 returns; no database, provider, or
  credential is opened. Engle-Granger uses normalized log levels and MacKinnon
  2010 N=2 finite-sample critical values without an approximate p-value. VAR
  exposes equation coefficients, lag matrices, and residual covariance.
  Granger compares restricted and unrestricted target equations with an exact
  F tail under the declared classical assumptions and makes no structural-
  causality claim. Frozen independent NumPy/SciPy vectors, missing/short/rank
  boundaries, direct/HTTP parity, no-store checks, and exact projection to
  byte-identical `2.38.0` validate the increment. Catalog `2.6.0` contains
  26 contracts; all 57 public names and prior variants remain unchanged.

- Registry `2.40.0` implements
  [ADR 0012](docs/adr/0012-compact-fmp-calendar-retention.md). Forward-only
  macro migration 0018 leaves all migration-0016 evidence intact, appends one
  receipt per material poll and only new/changed raw event versions, and keeps
  one replaceable latest-response cache. Exact replay writes nothing; A to B
  to A appends three corrections. The fixed Inspector exposes Current and
  History modes. The implementation itself did not run the provider, migrate
  the canonical store, repeat history, or alter either macro timer. On
  2026-08-24 the user separately authorized only canonical application;
  migration 0018 was applied at `2026-08-25T01:31:39.017401Z` with empty new
  relations, unchanged migration-0016 row counts, and no provider, credential,
  or scheduler action.

- Registry `2.41.0` adds the native local read-only
  `market.get_price_series` tool over the existing Stage 10 point-in-time
  reader. It resolves a ticker server-side and returns exactly four coherent
  typed series in open/high/low/close order; inclusive `start_date` and
  `end_date` are independently optional. The active inventory is 58 names,
  while the recovered compatibility target remains 57 and the frozen v1
  catalog stays byte-identical. Catalog `2.7.0` has 28 contracts. Exact
  projection restores byte-identical registry `2.40.0`; the increment adds
  no provider request, credential, migration, write path, scheduler, export,
  hosting, or deployment.

- Registry `2.42.0` adds the native local read-only
  `market.get_available_ticker` tool and a fixed no-URL subprocess interface
  for agents in other projects on this computer. Empty `{}` arguments return
  the deterministically ordered, 10,000-record-bounded current set of Stage 10
  FMP/provider-native instruments with a retrievable current price version and
  capture; optional `limit` can lower the bound. The result explicitly is not
  a live or historical point-in-time universe. The active inventory is 59
  names, catalog `2.8.0` has 30 contracts, and the frozen 57-name/v1 surface
  remains byte-identical. Exact projection restores registry `2.41.0` and
  catalog `2.7.0`. The bridge exposes dynamic `list`, `describe`,
  `manifest`, and strict-JSON stdin/stdout `call` commands with host-owned
  project, registry, and store routing. This increment adds no direct SQLite
  access, URL, provider request, credential, write, migration, scheduler,
  export, hosting, or deployment.

- Registry `2.43.0` adds one private Alpaca SPY option-surface collector over
  the existing Stage 10 identity and options schema. Its fixed four-request
  plan selects the listed expiration nearest 30 DTE inside 23-37 DTE, filters
  strikes to 80%-120% of an IEX SPY reference, and retains paper-account
  contracts with indicative option snapshots for inspection. It enforces one
  attempt, 10,000 rows, 8 MiB, and 120 seconds; network work completes before
  the market-store lock and exact semantic replay writes nothing. It adds no
  migration, public tool, export, deployment, or recovered market-job authority.
  The current user separately authorized a 15:55 America/New_York weekday host
  timer with an OPRA trading-calendar gate. It was linked and enabled on
  2026-08-25 after the focused offline gate and host status check passed; its
  first scheduled trigger is 15:55 EDT that day.

- Registry `2.44.0` adds one private, manual-only SEC core-fundamentals
  collector for fixed AAPL CIK `0000320193`. It makes exactly two
  single-attempt requests—submissions and CompanyFacts—then reuses the existing
  company schema and publisher to append issuer, filing, raw-fact, normalized
  fundamental, and filing-membership history. A fixed local read-only
  Inspector view exposes the normalized AAPL rows. This increment adds no
  migration, public tool, scheduler, export, hosting, or deployment.
  The explicitly authorized seed completed on 2026-08-25: two responses
  produced 1,026 filings, 1,552 normalized fundamental versions, and 682
  current Inspector rows across 10 metrics in `data/company.sqlite`.

- Registry `2.45.0` extends that private SEC path to the 519 Stage 10 equity
  symbols. A fixed zero-argument wrapper resolves the official SEC ticker map,
  deduplicates shared CIKs, and processes sequential submissions/CompanyFacts
  pairs at no more than five requests per second, with no retry, per-issuer
  isolation, 64 MiB/50,000-row issuer bounds, and a six-hour aggregate bound.
  AAPL continues to use its exact legacy semantic identity. The existing
  ten-metric reviewed map now supports annual 20-F/40-F facts, and the local
  read-only Inspector covers every populated issuer with an exact CIK filter.
  The user explicitly authorized one longest-available SEC CompanyFacts
  population and scheduled fetching; the implementation fixes the latter to
  a new hardened, non-persistent weekday 07:15 America/New_York host timer.
  The 2026-08-25 pass completed without exhausting its aggregate deadline,
  matched 517 of 519 equity symbols to 514 SEC issuers, and populated 476 of
  those issuers. It issued 1,011 requests and retained 467,858 filings plus
  571,162 normalized fundamental versions; history spans filing dates from
  1995-05-19 and selected fact periods from 2006-12-31. Thirty-eight issuer
  attempts failed independently, two symbols were unmatched, and one matched
  issuer failed ticker confirmation. Post-write integrity, foreign-key,
  lineage, and live Inspector checks passed. The timer was then linked and
  enabled without manually starting its service; future weekday invocations
  provide new attempts under the same no-retry-per-invocation rule. This
  increment adds no migration, public tool, export, hosting, or deployment.

- Registry `2.46.0` completes Step 1 of the post-restoration analytics plan:
  canonical read access. Explicit `2.0.0` variants of
  `macro.search_series`, `macro.describe_series`, and `macro.get_series`
  expose the generic and official-vintage macro cores without crossing their
  authority boundary. The series reader implements stored-evidence
  `latest`, `as_of`, and evidenced `first_release` selection. Native
  `market.get_volume_series` reuses the verified Stage 10 row selector, and
  native `macro.get_release_calendar` composes retained legacy and
  incremental calendar evidence for `latest` or `as_of` retrieval. The active
  inventory is 61 names and catalog `2.9.0` has 40 contracts; the 57-name/v1
  compatibility surface remains byte-identical. Exact projection restores
  registry `2.45.0` and catalog `2.8.0`. This increment adds no portfolio or
  position tool, migration, dataset owner, collector, provider request,
  credential access, canonical write, scheduler, export, hosting, or
  deployment.

- Registry `2.47.0` completes Steps 2-4 of the post-restoration analytics
  foundation. Explicit `2.0.0` successors implement Stage 10 return-series
  quality auditing and time-series transformations. Four additive native
  statistics tools implement distribution diagnostics, sample covariance and
  correlation, explicitly seeded deterministic bootstrap intervals, and
  deterministic covariance- or correlation-basis PCA. All six tools are
  store-free and consume only caller-supplied typed trailing-return series;
  they reject or explicitly report incomplete, truncated, or incompatible
  inputs according to their contracts. The active inventory is 65 names,
  catalog `2.10.0` has 52 contracts, and exact projection restores registry
  `2.46.0` and catalog `2.9.0`. The frozen v1 surface remains byte-identical.
  This increment adds no portfolio or position tool, provider request,
  credential, canonical write, migration, scheduler, export, hosting, or
  deployment.

- Registry `2.48.0` adds only the store-free
  `market.technical_indicators@2.0.0` successor. It consumes compatible typed
  Stage 10 OHLCV inputs and computes one of 15 deterministic indicator
  specifications with explicit warm-up and undefined missingness. Catalog
  `2.11.0` has 54 contracts; exact projection restores registry `2.47.0` and
  catalog `2.10.0`. It adds no provider, credential, store, migration,
  scheduler, export, hosting, or deployment scope.

- Registry `2.49.0` adds one private, manual-only Alpaca ETF option-surface
  grid for `SPY`, `QQQ`, `IWM`, `DIA`, and the eleven sector ETFs. Exact DTE
  targets are `1`, `2`, `3`, `7`, `14`, `30`, `60`, `90`, `180`, and `365`;
  nearest positive expiries are deterministic and duplicate target mappings
  are captured once. It reuses the existing option schema and replay-safe
  publisher, and the local Inspector gains a fixed read-only
  `options-surfaces` view. At registry `2.49.0`, the broad collector had no
  registry job or host timer and the separately authorized timer remained
  SPY-only. The implementation
  received one separately authorized bounded live attempt. That attempt made
  17 requests, failed all 15 symbols before any option-chain request, and
  published zero canonical rows. It was not retried. See
  [the additive IWM and broad-options evidence](docs/rebuild/STAGE10_IWM_EXTENSION_EVIDENCE.md).
- Registry `2.50.0` adds one private, manual-only FMP IWM full-history
  collector over the existing Stage 10 evidence, instrument, universe, and
  daily-price relations. It derives an exact 96-member ETF successor from the
  frozen 95-member snapshot plus IWM without changing the sealed
  629-instrument cohort or single-name rule. One operation is bounded to one
  request, one attempt, 30,000 rows, 16 MiB, and 45 seconds; preflight occurs
  before provider access and the response is published atomically outside the
  network phase. Exact projection restores registry `2.49.0`. It adds no
  migration, dataset, job, timer, public tool, dashboard, export, hosting, or
  deployment. The separately authorized live operation completed with 1,254
  daily IWM rows from `2021-08-30` through `2026-08-27` and the exact
  96-member successor. Immutable integrity, foreign-key, lineage,
  frozen-cohort, and Inspector checks passed; the private completion state
  prohibits another invocation. See
  [the additive IWM evidence](docs/rebuild/STAGE10_IWM_EXTENSION_EVIDENCE.md).
- Registry `2.51.0` adds only a private, manual-only fixed IWM
  missing-interval collector declaration. It grants no new provider or
  canonical-write authority, and exact projection restores `2.50.0`.
- Registry `2.52.0` adds explicit store-free `2.0.0` successors for seven
  research, diagnostics, and forecast tools over caller-supplied compatible
  Stage 10 returns. Catalog `2.12.0` preserves every frozen v1 default.
- Registry `2.53.0` adds `company.search_filings@2.0.0` with exact-CIK,
  cutoff-bound cursor pagination, removing the legacy 500-row dead end.
- Registry `2.54.0` adds `market.search_instruments@2.0.0` for retained
  Stage 10 FMP identity search and
  `company.get_share_count_history@2.0.0` for reviewed SEC outstanding,
  basic weighted-average, and diluted weighted-average share facts. Catalog
  `2.14.0` has 74 contracts; the active inventory remains 65 logical names
  with frozen v1 defaults. These successors are read-only and add no provider,
  credential, migration, write, scheduler, export, hosting, or deployment
  scope. Exact projection restores byte-identical registry `2.53.0`.
- Registry `2.55.0` adds only
  `market.technical_indicators@2.1.0`. It preserves the frozen v1 default and
  exact v2.0 contract, and adds the causal `supertrend_ai` adaptation over
  caller-supplied typed high, low, and close series. Catalog `2.15.0` has 76
  contracts and 30 explicit variants across the unchanged 65 logical names.
  Exact projection removes only v2.1 and restores byte-identical registry
  `2.54.0`; no provider, credential, store, migration, write, scheduler,
  export, hosting, or deployment scope is added.
- Registry `2.56.0` adds only
  `market.technical_indicators@2.2.0`. It preserves the frozen v1 default and
  exact v2.0/v2.1 contracts, and adds the causal
  `swing_structure_forecast` adaptation over caller-supplied typed high, low,
  and close series. Catalog `2.16.0` has 78 contracts and 31 explicit variants
  across the unchanged 65 logical names. Exact projection removes only v2.2
  and restores byte-identical registry `2.55.0`; no provider, credential,
  store, migration, write, scheduler, export, hosting, or deployment scope is
  added.


- Registry `2.57.0` adds fifteen explicit read-only
  investment-analysis v2 successors: query-bound macro calendar pagination,
  explicit-ticker cross-sectional endpoint performance, first-versus-latest
  revision and ex-post surprise standardization, direct rates/liquidity/credit
  and regime readers, raw liquidity-credit research state, retained Stage 11
  energy readers, and normalized company facts. At that revision the inventory
  remained 65 logical names; catalog `2.17.0` had 108 contracts, 40 version policies,
  and 46 variants. These are bounded source-native readers or direct
  calculations, not portfolio tools, opaque composite scores, classifiers, or
  investment recommendations. Exact projection restores `2.56.0`/`2.16.0`;
  no provider request, canonical write, migration, scheduler, export, hosting,
  or deployment scope is added.


- Registry `2.58.0` adds only
  `market.technical_indicators@2.3.0`. It preserves every predecessor and
  adds Pine-equivalent KDJ over caller-supplied typed high, low, and close
  series. Catalog `2.18.0` has 110 contracts, 40 version policies, and 47
  variants across the unchanged 65 logical names. Exact projection removes
  only v2.3 and restores byte-identical `2.57.0`; no provider, credential,
  store, migration, write, scheduler, export, hosting, or deployment scope is
  added.

- Registry `2.59.0` completes the fast-track stationarity and structural-break
  compatibility fix and adds five explicit successors: Williams Vix Fix
  v2.4, market breadth/risk analytics v2.1, fixed-lag seasonality for both
  retained energy readers v2.1, and normalized company ratios v2.1. Catalog
  `2.19.0` has 120 contracts, 40 version policies, and 52 variants across the
  unchanged 65 logical names. Exact projection removes only those five
  variants and restores byte-identical registry `2.58.0` and catalog
  `2.18.0`; no portfolio semantics or new operational authority is added.

- Registry `2.60.0` adds only
  `market.technical_indicators@2.5.0`, a store-free WaveTrend-with-crosses
  calculation over caller-supplied typed high, low, and close series. Catalog
  `2.20.0` has 122 contracts, 40 version policies, and 53 variants across the
  unchanged 65 logical names. Exact projection removes only v2.5 and restores
  byte-identical registry `2.59.0` and catalog `2.19.0`; no provider, store,
  migration, scheduler, export, hosting, or deployment scope is added.

- Registry `2.61.0` adds only
  `market.technical_indicators@2.6.0`, a store-free Parabolic SAR calculation
  over caller-supplied typed high, low, and close series. It accepts finite
  Decimal `start`, `increment`, and `maximum` values defaulting to `0.02`,
  `0.02`, and `0.2`, and returns one `parabolic_sar` series. Catalog `2.21.0`
  has 124 contracts, 40 version policies, and 54 variants across the unchanged
  65 logical names. Exact projection removes only v2.6 and restores
  byte-identical registry `2.60.0` and catalog `2.20.0`; no new operational
  authority is added.

- Registry `2.62.0` adds only
  `market.technical_indicators@2.7.0`, the store-free rolling-regression-line
  successor. Catalog `2.22.0` has 126 contracts, 40 version policies, and 55
  variants across the unchanged 65 logical names. Exact projection restores
  byte-identical registry `2.61.0` and catalog `2.21.0`.

- Registry `2.63.0` adds the separate repeatable current FMP news successor:
  migration `news:0006_fmp_stock_latest_current`, two private datasets,
  manual-only collector `fmp.news.stock_latest_current`, and local read-only
  `news.search@2.0.0`. Catalog `2.23.0` has 128 contracts, 41 version
  policies, and 56 variants; exact projection restores `2.62.0`/`2.22.0`.
  The bounded 2026-08-30 proof applied migration 0006 and retained 229 articles
  from one request without retry.

- Registry `2.64.0` adds migration `news:0007_current_multi_source`, two
  active private datasets, collector `news.current_multi_source`, and read-only
  `news.search@2.1.0`. Forward migration
  `news:0008_adopt_fmp_news_legacy` owns the pre-existing
  `fmp_news_articles` relation as inactive private evidence behind an exact
  schema guard, with no consumer or source ID. The bounded 2026-08-30 16:00 UTC
  proof completed all 20 requests without retry and retained 1,069 generic
  articles; all eight source steps succeeded. On 2026-08-30 the reviewed
  hourly per-user timer was linked, enabled, and started. That check recorded
  active/waiting for its first normal `:10` UTC trigger; activation did not run the service,
  contact a provider, or write a store.

- Registry `2.65.0` adds market migration
  `market:0011_option_raw_evidence` and one private raw-response evidence
  dataset bound to both Alpaca option collectors. It corrects Alpaca's
  explicit ordinary 100-share delivery classification, retains exact future
  response bytes, and leaves true adjusted contracts analytically excluded.
  Exact projection restores byte-identical registry `2.64.0`; the tool
  catalogs are unchanged. The retained market store was migrated on
  2026-08-30 local time without a provider call or timer change.

- Registry `2.66.0` adds cursor-paginated `news.search@2.2.0` and eight
  bounded local read-only news/research tools for source status, item history,
  candidate clusters, provider-symbol coverage, attention, deterministic
  event and sentiment labels, and retrospective raw market impact. Catalog
  `2.25.0` has 148 contracts across 73 logical names. The increment adds no
  provider call, canonical write, migration, scheduler, deployment, article
  body exposure, or causal/abnormal-return claim. Exact projection restores
  byte-identical registry `2.65.0` and catalog `2.24.0`.

- Registry `2.67.0` adds retained-only
  `data.get_dataset_status@1.0.0` plus explicit `2.0.0` successors for option
  capture search, contract search, and coherent surface snapshots. Catalog
  `2.26.0` has 156 contracts across 74 logical names. Status never probes a
  provider, credential, or scheduler; options never mix capture cohorts and
  keep raw responses private. The increment adds no provider call, canonical
  write, migration, timer, deployment, or public-network surface. Exact
  projection restores byte-identical registry `2.66.0` and catalog `2.25.0`.

- Registry `2.68.0` adds six bounded, credential-free, `manual_only` private
  macro-history collectors for CFTC TFF and disaggregated futures-only history,
  Treasury securities auctions, NY Fed Primary Dealer Statistics, and Federal
  Reserve H.8 and SLOOS. They reuse the three existing macro output datasets and
  add no migration, job, scheduler, public tool, or catalog contract. The
  declaration itself authorizes no provider execution. Exact projection restores
  byte-identical registry `2.67.0` at SHA-256
  `a80b0e06db95968c9fd49cd3d90054b709c57895993a28b512ba2550e162f325`; catalog `2.26.0` remains unchanged.

- On 2026-08-30 the user explicitly authorized updating the enabled,
  legacy-named `quant-data-alpaca-spy-options.timer` in place to invoke the
  fixed 15-ETF option grid at the then-current 15:55 America/New_York weekday
  cadence. On 2026-08-31 the user authorized moving that same fixed timer to
  16:20 America/New_York so collection begins after the latest ordinary
  ETF-option session. It remains non-persistent with no retry or catch-up.
  One invocation is capped at 362 single-attempt requests, 900,016 rows,
  256 MiB, and 900 seconds with a 16-minute host timeout. Exact provider bytes
  are retained before normalization; partial grids exit nonzero. The registry
  collector remains `manual_only`, and the 18:00 Stage 12E FMP job is separate.

- On 2026-08-23 the user separately authorized a fourth host-level recurring
  exception, `quant-data-macro-current-refresh.timer`, at 18:30
  America/New_York on weekdays. On 2026-08-24 the user expanded it to
  twenty-three operations and a fixed request cap of 31 by adding the
  credential-free CFNAI and INDPRO requests; ECI rides the existing BLS
  request. It has no retry, uses the fixed `data/macro.sqlite` target, and
  preserves semantic no-write behavior for unchanged content. Its established
  NY Fed and Chicago requests carry the added series,
  the existing BEA-vintage timer carries GDI in the same workbook request, and
  the aggregate adds the bounded EIA electricity-retail, weekly crude-oil-
  stock, weekly total-motor-gasoline-stock, distillate-fuel-oil-stock,
  finished-motor-gasoline-product-supplied, Federal Reserve IORB/target-bound,
  Treasury Debt to the Penny, Monthly Treasury Statement fiscal-balance,
  and fixed BEA personal-income/outlays operations. The BEA extension adds one
  private collector while reusing the existing provider family, credential
  resolver, datasets, publisher, and timer cadence; it adds no migration or
  public route and does not change the three other timer cadences.
- SQLite remains the authoritative operational store unless benchmarks and an
  accepted decision record justify a change.
- The four operational boundaries are market, macro, company, and news.
- Immutable evidence, canonical versioned facts, and derived research outputs
  are separate layers.
- Point-in-time availability is a shared contract, not a domain-specific
  convention.
- The public 57-tool inventory is a compatibility target. Internal
  implementation should use a smaller composable core.
- Scheduling follows physical database paths and is restored only after manual
  workflows are deterministic and idempotent.

## 3. Stage overview

| Stage | Outcome | Live data allowed? | Exit decision |
| --- | --- | --- | --- |
| 0 | Planning package accepted | No | Architecture, ADRs, contracts, and scope are approved |
| 1 | One complete vertical slice | No; fixtures only | End-to-end contracts work on temporary stores |
| 2 | Four-store persistence foundation | No | Independently verified offline evidence |
| 3 | Bounded market and macro restoration | No; reviewed fixtures only | Independently verified offline evidence; Stage 4 requires separate authorization |
| 4 | Company, news, and options restoration | No; synthetic fixtures only | Independently verified offline evidence; Stage 5 is next |
| 5 | Composable 57-tool platform | No live access; intraday remains disabled | Independently verified offline evidence; bounded Stage 6 portal primary gate passed |
| 6 | Bounded local portal (four fixed routes) | No mutation through UI | Accepted offline evidence with explicit browser-automation waiver |
| 7 | Manual fixture job rehearsal and operational readiness | No live access; jobs disabled | Independently verified offline evidence; bounded Stage 8 is separately authorized |
| 8 | One fixture-only JSON snapshot and static Atlas | Explicit SQLite online-backup copies only | Accepted after independent offline verification with explicit browser-automation waiver |
| 9 | One manual FMP SPY non-production backfill and candidate receipt | Only the exact approved request after preflight | Bounded live population independently verified; no promotion |
| 10 | Frozen 629-symbol FMP market-history base plus eight-window extension | Only the completed exact candidate requests | Retained private candidate receipts complete and independently re-inspected; no repeat or promotion |
| 11 | Bounded BEA NIPA and EIA retail/weekly macro history | Only the exact serialized candidate requests | Retained private candidate receipt complete; no repeat or promotion |
| 12 | Serial Market v1 operationalization | Completed Stage 12C manual slice and Stage 12D fixed-target read-only proofs | 12A/12B independently verified; [Stage 12C](docs/rebuild/STAGE12C_EVIDENCE.md) and [Stage 12D](docs/rebuild/STAGE12D_EVIDENCE.md) complete and independently verified; 12E closed |

Stages are ordered by dependency, not calendar duration. A later stage may be
designed in parallel, but implementation does not cross an unmet exit gate.
The dependency-aware lanes, serialized hotspots, four-thread cadence, and
integration checkpoints are defined in the
[parallel execution plan](docs/rebuild/PARALLEL_EXECUTION.md).

## 4. Stage 0 — Accept the planning package

### Deliverables

- Accept or revise [ARCHITECTURE.md](ARCHITECTURE.md).
- Accept the foundational ADRs under [docs/adr](docs/adr).
- Review every focused specification under
  [docs/rebuild](docs/rebuild/README.md).
- Resolve terminology conflicts between the early recovery narrative and the
  superseding evidence in Sections 18–20 of `plan.md`.
- Classify unresolved details as required before the vertical slice, required
  before a later domain stage, or safe to defer.

### Exit gate

- Every planned component has one owner and one authoritative document.
- The vertical-slice scope contains no unresolved semantic decision.
- ADR statuses move from `Proposed` to `Accepted` only with user approval.
- A fresh Git repository may then be initialized; no lost history is inferred.

## 5. Stage 1 — Prove one complete vertical slice

Implementation status: **Implemented**. Reproducible results and deterministic
receipts are recorded in the
[Stage 1 acceptance evidence](docs/rebuild/STAGE1_EVIDENCE.md).

The first implementation slice is defined in
[VERTICAL_SLICE_SPEC.md](docs/rebuild/VERTICAL_SLICE_SPEC.md). It deliberately
includes both current-state market data and revision-aware macro data so the
architecture cannot pass by implementing only the simpler case.

### Deliverables

- A minimal registry containing only the slice's datasets, stores, migrations,
  tool exposure, and quality rules.
- Temporary market and macro SQLite stores with independent migration ledgers.
- One daily-price fixture family, including an exact replay and correction.
- One macro-vintage fixture family, including first, revised, and as-of states.
- Immutable evidence records and canonical facts linked by stable identity.
- Shared availability and missingness semantics.
- A minimal read-only tool path using shared primitives.
- A minimal local inspection page or endpoint using the same read path.
- Deterministic clean rebuild and test commands.

### Exit gate

- No test can fall back to a default or live database path.
- Exact replay is a no-op; correction and revision behavior are explicit.
- An as-of cutoff cannot see evidence captured later.
- Strict JSON, resource limits, read-only routing, and error envelopes pass.
- Destroying and recreating temporary stores yields the same logical result.
- The architecture review finds no special-case shortcut that would fail when
  a third domain is added.

## 6. Stage 2 — Establish the four-store foundation

Implementation status: **Implemented — fixture-validated**. The approved
offline evidence is recorded in [Stage 2 acceptance evidence](docs/rebuild/STAGE2_EVIDENCE.md).
It is a bounded foundation; Stage 3 required its own explicit authorization
and acceptance evidence.

### Deliverables

- Independent market, macro, company, and news initialization.
- Shared control-plane conventions for migrations, dataset registration,
  ingestion runs, artifacts, snapshots, and quality results.
- One accepted machine-readable registry as the source of store ownership and
  cross-cutting configuration.
- Registry validation that rejects duplicate IDs, unknown stores, migration
  collisions, mutable identity fields, and unsafe routing.
- Four distinct operational stores only. Unified runtime compatibility is
  retired; duplicate physical store identities fail closed.
- Read-only connection factories and host-selected database routing.

### Recorded Stage 2 foundation

- Registry revision `2.0.0` fixes four distinct operational stores, ten
  fixture-validated store-local migrations, exactly 57 reserved compatibility
  names, and only the two validated public tools.
- The shared control plane covers artifacts, snapshots, quality results,
  ingestion-run outputs, and immutable failure audits. Company and news have
  only empty-domain foundations.
- The physical layer contract is exactly `evidence`, `canonical`, and
  `derived`. A forward-preserving Stage 2 table rebuild uses temporary
  foreign-key disablement only inside its atomic boundary and requires
  `foreign_key_check` before ledger advancement.
- Immutable identities resist `INSERT OR REPLACE` even with recursive triggers
  off; library connections enable recursive triggers, running-run identities
  and failed run IDs cannot be reused, and health reconciles exact reviewed
  SQLite schema SQL.
- Routing rejects unified and duplicate physical identities; locks use the
  resolved physical store identity and deterministic multi-lock order.
- Cross-store composition applies a declared `captured_at` cutoff and reports
  its honest `best_effort_multi_store` cohort semantics rather than claiming a
  cross-file atomic read.
- Read-only health reconciliation and WAL-safe SQLite online backup/restore
  prove equal source, backup, and restored logical evidence without mutating
  the source cohort.
- Live providers, scheduler installation or jobs, exports or Atlas, promotion,
  and destructive storage operations are excluded. The bounded Stage 3 fixture
  scope is separately recorded below.

### Exit gate

- Fresh and repeated initialization is idempotent for all four stores.
- Applied migration identities are immutable and verifiable.
- Store ownership agrees across migrations, registry, tools, dashboard,
  scheduler plans, and exports.
- Cross-store composition uses bounded application-level joins under one
  declared cutoff.

Stage 2 evidence records fixture validation and the independent verifier pass.
It did not by itself authorize Stage 3 executable work.

## 7. Stage 3 — Restore market and macro domains

Status: **Implemented — fixture-validated and independently verified.** The
user authorized this bounded offline fixture scope
after Stage 2. Its deterministic evidence is recorded in the
[Stage 3 acceptance evidence](docs/rebuild/STAGE3_EVIDENCE.md). This status
does not open Stage 4, live collection, jobs, exports, or new public tools.

### Fixture-validated scope

- Registry revision `2.1.0` declares 21 fixture-validated migrations, 19
  datasets, 14 collectors, the same two validated public tools, 57 reserved
  names, and no jobs or exports.
- Market adds canonical instrument/catalog evidence, dated provider identifiers,
  effective-dated classifications, controlled universe membership, complete
  scope tombstones and restoration, and exactly the nine recovered FMP index
  identities. The Stage 1 daily-price fixture remains preserved; no live price
  provider is restored.
- Macro adds a generic series catalog and observations, GDP vintage provenance,
  Treasury curves, an economic calendar, aggregate-only SOMA evidence and
  summaries, EIA electricity-retail and weekly-fundamentals contracts, and a
  completed U.S. recession chronology. BLS, BIS, Chicago Fed, and BEA fixtures
  exercise the generic semantic gate; they do not connect to providers.
- The gate imports 25 reviewed synthetic Stage 3 fixtures (29 including the
  preserved Stage 1 fixtures), rejects incomplete or internally inconsistent
  complete EIA retail captures without mutation, verifies volatile-only
  BLS/BEA metadata replays as no-ops, and compares source, backup, restored,
  and two-root path-free evidence.

### Market and macro contract coverage

1. Canonical instruments and dated provider identifiers.
2. Controlled universes and classifications.
3. Preserved daily-price evidence, canonical facts, correction history, and
   quality checks from the Stage 1 vertical slice.
4. The nine recovered FMP index identities.
5. Effective-dated universe membership before survivorship-sensitive
   cross-sectional research is presented as safe.

### Macro sequence

1. Canonical series catalog and dimensions.
2. Versioned observations, releases, artifacts, and snapshot membership.
3. RTDSM and GDP vintage behavior.
4. Treasury, funding, liquidity, and recession chronology.
5. BLS, BIS, Chicago Fed, BEA, SOMA, and EIA families.

### Exit gate

- Identifier changes do not change canonical identity.
- Latest, as-of, and defensible first-release fixtures differ as expected.
- Date-only values do not acquire invented timestamps.
- Corrections, partial captures, missing values, tombstones, and restoration
  are all fixture-tested.
- No CUSIP-level SOMA storage or source path exists.

The primary fixture gate and read-only SolUltra verification passed these
bounded checks and reproduced the acceptance evidence. That completes `G3` but
does not itself authorize Stage 4.

## 8. Stage 4 — Restore company, news, and options

Status: **Implemented, fixture-validated, and independently verified.** The
user authorized this bounded synthetic fixture scope after `G3`. Its
deterministic receipts and verifier result are recorded in the
[Stage 4 acceptance evidence](docs/rebuild/STAGE4_EVIDENCE.md). `G4` is
complete. Stage 5 subsequently passed its primary offline fixture gate.

### Company

- CIK-based issuer identity and dated security/provider links.
- Immutable SEC accessions and captured CompanyFacts.
- Versioned normalized fundamentals with mapping-version lineage.
- Separate instant and weighted-average share semantics.
- Versioned actions, expectations, earnings events, and guidance.

### News

- Immutable logical items, raw evidence, changed-content versions, snapshot
  membership, labels, retractions, and point-in-time search.
- FTS remains a derived index over canonical content, not the source of truth.

### Options

- The recovered migration 0029/0030 domain model.
- One immutable capture, underlying, environment, and resolved feed per
  surface.
- Explicit missing contracts and a complete exclusion ledger.
- Synchronized underlying, rate, dividend, and expiry inputs.

### Fixture-validated scope

- Registry revision `2.2.0` declares 30 fixture-validated migrations, 33
  datasets, 19 collectors, the same two validated public tools, 57 reserved
  names, and no jobs or exports.
- Stage 4 adds nine deliberate forward migrations: two market options, five
  company, and two news resources. Their frozen identities, paths, and hashes
  are recorded in the [migration reconstruction map](docs/rebuild/MIGRATION_RECONSTRUCTION.md).
- The primary gate imports exactly 17 reviewed synthetic fixtures: seven
  company, five options, and five news. It passes 164 full-suite tests and 27
  focused Stage 4 tests, with deterministic two-root strict-JSON evidence.

### Exit gate

- Company ticker changes and joint filings preserve issuer/accession identity.
- A news as-of query excludes later local captures.
- Option analysis cannot combine capture IDs, feeds, environments, or
  nonstandard deliverables silently.
- All domain tests use temporary stores and redacted fixtures.

The primary fixture gate and independent SolUltra verification completed
these checks. `G4` is complete.

## 9. Stage 5 — Restore the composable tool platform

Status: **Implemented, fixture-validated, and independently verified.**

The contract is defined in
[TOOL_PLATFORM_SPEC.md](docs/rebuild/TOOL_PLATFORM_SPEC.md). Deterministic
receipts and the primary-gate result are recorded in the
[Stage 5 acceptance evidence](docs/rebuild/STAGE5_EVIDENCE.md).

### Deliverables

- Shared typed primitives for discovery, retrieval, transformation, alignment,
  statistics, econometrics, research contracts, and strict serialization.
- Public compatibility adapters for the reviewed 57 tool names.
- Registry-derived input/output schemas, examples, limits, and routing.
- Tool-version and data-lineage metadata in every research response.
- Additive compatibility and explicit deprecation rules.

### Fixture-validated scope

- Registry revision `2.3.0` and schema version `1.1.0` expose all 57 reviewed
  names through generated input and output schemas and closed routing.
- The two Stage 1 contracts retain their historical projection; the other 55
  are explicit forward reconstructions. Unsupported fixture semantics return
  `not_established`, and the live intraday capability is disabled.
- Direct and loopback HTTP calls share typed validation, strict JSON, bounded
  concurrency/deadlines/cancellation, deterministic receipts, and read-only
  four-store access.
- The primary gate passed 207 tests and a deterministic two-clean-root rebuild
  without adding migrations, jobs, exports, live providers, or UI code.

### Exit gate

- Every public schema rejects unknown properties.
- Caller input cannot choose database paths or submit SQL.
- Equivalent HTTP and in-process calls produce equivalent contracts.
- Every limit is enforced before expensive work begins.
- Results are deterministic, read-only, finite JSON with explicit exclusions
  and warnings.

The primary fixture gate and independent SolUltra verification completed these
checks. `G5` is complete. The bounded Stage 6 portal primary gate has since
passed; its unavailable browser-automation check was explicitly waived by the
user before the Stage 6 commit and Stage 7 authorization.

## 10. Stage 6 — Restore portal and research surfaces

Status: **Implemented and accepted: bounded offline four-route portal.** The
offline executable verifier passed; on 2026-08-10 the user explicitly accepted
the unavailable browser-automation check and authorized the Stage 6 commit.

The shared visual and interaction language is defined in
[UI_VISUAL_DIRECTION.md](docs/rebuild/UI_VISUAL_DIRECTION.md). The bounded
implementation establishes the first local application shell and registry
exposures. The broader chart/algorithm/backtest surface remains deferred.

### Implemented bounded scope

- One persistent local application shell using the accepted Inter-based,
  restrained institutional light token set.
- Four fixed local-private routes: Overview (`/`), GDP Vintages
  (`/gdp-vintages`), Tables (`/table-inspector`), and Agent Tools
  (`/agent-tools`).
- Frozen Stage 6 registry projection `2.4.0` and schema `1.2.0` dashboard exposures
  with fixed datasets, relations, filters, server-owned sort fields,
  pagination, tool dependencies, and API routes.
- Read-only, query-only services and loopback boundary checks, including
  source/restored store mutation fingerprints and fixed security headers.
- Locally bundled CSS, JavaScript, and pinned Inter 4.1 font/OFL license
  assets; no reference-site, remote-font, analytics, or tracker asset.
- No jobs, exports, live providers, runtime network work, scheduler
  installation, promotion, hosting, Atlas, or destructive storage operation.

### Formal analytical scope remains deferred

Charts, shared indicator research, algorithms, and walk-forward/backtest
reports are not implemented or claimed by this bounded portal. They require
their own causal-timing, cost, canonical-backend, golden-vector, and
acceptance evidence before any implementation decision. Atlas remains Stage 8
work and cannot be opened by this portal's primary gate.

### Accepted bounded exit

The bounded four-route portal exit is accepted. Offline independent checks
proved the read-only API, mutation neutrality, fixed registry contracts,
strict JSON handling, local assets, contrast, and deterministic two-root
evidence. The Codex browser controller could not attach even though the local
portal was visibly running; on 2026-08-10 the user explicitly waived that
automation check, authorized the Stage 6 commit, and opened bounded offline
Stage 7 work. The following broader analytical requirements remain deferred
and are not claimed by this Stage 6 commit:

- The browser has no ingestion or raw-SQL capability.
- All database connections are read-only and query-only.
- Warm-up and missing observations render as gaps rather than zero.
- Indicator calculations have golden vectors and one canonical backend.
- Backtests use training-only transformations, explicit decision/execution
  times, costs, benchmarks, and deterministic ties.
- Visual-regression, responsive, keyboard, reduced-motion, contrast, and
  locally bundled font checks pass without any reference-site runtime asset.

## 11. Stage 7 — Restore scheduling and operations

Status: **Implemented and independently verified for bounded offline manual
fixture rehearsal.** The accepted
evidence is recorded in
[Stage 7 acceptance evidence](docs/rebuild/STAGE7_EVIDENCE.md).

The contract is defined in
[SCHEDULING_AND_LOCKING.md](docs/rebuild/SCHEDULING_AND_LOCKING.md).

### Implemented bounded scope

- Canonical registry `2.5.0` / schema `1.3.0` declares exactly eight
  disabled `manual_fixture_only` jobs, derived write-store sets, bounded
  step graphs, and zero exports.
- Deterministic, path-free dry-run plans create no store, state, lock, provider,
  or scheduler side effect.
- All fixture candidates are parsed and sealed before acquisition of the
  complete physical-store lock session; publication reuses that session and
  does not nest locks.
- Retry classes, active step timeouts, dependency policies, aggregate sysexit
  precedence, and lock/I/O/configuration failures are bounded and receipt-safe.
- Private logs, optional monthly markers, and receipts are immutable and
  atomically published with the receipt last; a publication failure cannot
  report success.
- The restricted manual wrapper requires explicit initialized synthetic stores
  and an explicit private state root. It preserves aggregate exit codes.
- SQLite online backup/restore rehearsal proves integrity, foreign keys,
  read-only equality, and source mutation neutrality.

### Primary exit evidence

The primary gate passed 277 offline tests, including 43 operations tests and
three deterministic Stage 7 integration checks. It proves changed then
unchanged real fixture execution, exact replay no-write behavior, deterministic
multi-lock ordering/release, timeout and lock-contention handling, private
evidence safety, two-root equality, and nonempty-root refusal.

No live provider, runtime network, scheduler installation/start/update/removal,
export, promotion, hosting, Atlas, default path, or destructive operation is
authorized or implemented. Recovered external task definitions, calendars,
identity, and timezone remain unresolved; the eight jobs stay disabled. The
bounded Stage 8 profile is separately authorized, but it does not authorize
live providers, scheduler installation, hosting, or operational promotion.

## 12. Stage 8 — Derived JSON snapshot and Atlas

Status: **Complete for the bounded profile — primary offline fixture and
independent SolUltra verification passed; the unavailable in-app
browser-automation check was explicitly waived on 2026-08-14.** The contract is
defined in [ANALYTICAL_EXPORTS.md](docs/rebuild/ANALYTICAL_EXPORTS.md); its
accepted evidence record is [STAGE8_EVIDENCE.md](docs/rebuild/STAGE8_EVIDENCE.md).

This is a deliberate forward reconstruction, not a claim of recovered Atlas
source/package parity or 13-dataset historical coverage. It does not adopt
Parquet or DuckDB: their broader benchmark gate remains available for a future
separately authorized profile.

### Bounded profile

- Canonical registry `2.6.0` / schema `1.4.0` declares exactly one export:
  `atlas.fixture_snapshot` / `atlas.fixture_snapshot.core_v1`, manual-only,
  fixture-only, JSON, no network, no hosting.
- The only reciprocal datasets are market daily prices, macro GDP vintages,
  company issuers, and news items. Their four deterministic projections use
  explicit registered fields, ordering, schema, public exclusions, and bounds.
- The aware UTC export-start cutoff applies the shared availability-at-or-before
  comparator. Date-only source values retain their `completed_date` precision;
  future-ineligible evidence cannot alter an earlier revision.
- The publication cap is 250 rows / 262144 bytes per chunk and 12000 rows,
  8 MiB, and 30 seconds total. Non-finite values, ambiguous conversions, partial
  scope, unsafe roots, wrong stores, duplicate physical identities, and missing
  receipts fail closed.
- The exporter receives explicit store and output roots only. It takes complete
  deterministic physical locks, captures a SQLite online backup for each store,
  releases locks, and reopens only the copies query-only. The cohort states
  `cross_store_atomic: false` and exposes per-store receipts/coordination data.
- It stages under an exact unique child, fully validates JSON chunks/manifests,
  publishes an immutable revision, atomically replaces only the derived
  `current` pointer, and writes a private receipt. Operational stores are never
  promoted or mutated.
- Atlas is a dependency-free static snapshot consumer using the local Stage 6
  token system and Inter/OFL assets. It has no API or operational-store
  connection; `.vite` is excluded from source; no package manager, hosting, or
  deployment is part of this stage.

### Completion gate — accepted with browser-automation waiver

- The full dependency-free suite and deterministic two-root Stage 8 CLI run on
  explicit empty temporary roots and preserve all Stage 1 through Stage 7
  historical projections/evidence.
- Source stores are fingerprinted before and after source copies, queries,
  publication, retries, and failure injection to prove zero mutation.
- Independent read-only queries prove row identity, deterministic ordering,
  cutoff/PIT/date-only behavior, strict JSON, checksums, bounds, and range
  invariance; public artifacts contain no private fields.
- Pre-promotion and pointer/receipt failures preserve the last validated
  `current` snapshot; no immutable revision is overwritten.
- Atlas works from its static public tree with no remote asset, database/API,
  provider, scheduler, or write path.
- Independent SolUltra verification inspected the integrated worktree and
  recorded exact offline evidence/results. The in-app browser client remained
  unavailable before launch; the user explicitly waived that external
  automation limitation on 2026-08-14 and accepted the bounded gate. Responsive,
  keyboard, focus, reduced-motion, zoom, and runtime-network browser
  observations remain unrecorded and are not represented as passing evidence.

## 13. Stage 9 — Bounded FMP non-production backfill (candidate-only)

Status: **Bounded live population and candidate receipt independently
verified.** The exact scope and evidence are recorded in
[Stage 9 evidence](docs/rebuild/STAGE9_EVIDENCE.md). This authorization is
intentionally narrower than the original general repopulation/promotion
placeholder.

### Authorized scope

- One provider-specific manual FMP daily OHLCV request for `SPY`, inclusive
  `2026-07-01` through `2026-07-31`.
- One collector, `fmp.market.daily_price_backfill`, using the environment name
  `FMP_API_KEY` only, one request, and no retry.
- One exact non-production root:
  `/home/volatility/quant-data-nonprod/stage9-fmp-spy-202607`.
- One additive market-only migration,
  `market:0009_fmp_daily_price_backfill`, and isolated FMP evidence,
  instrument, version, and current-price relations.
- Coverage, row-count, sample-value, gap, response/provenance, integrity,
  migration, and dataset-quality reconciliation.
- Unchanged replay proof, SQLite online backup/clean restore proof, and a
  scratch-restoration-only correction rehearsal.
- A private candidate-only receipt tying the request scope, code/registry,
  migration, store snapshots, reconciliation, and configuration-name metadata
  together without a credential value.

### Explicitly excluded

- Any other symbol, range, endpoint, provider, request retry, scheduler, or
  background operation.
- Tool, dashboard, Atlas, host, deployment, export, or public data exposure.
- Operational-store promotion, use of a default store path, old-store
  retirement, or destructive cleanup.
- A claim that an FMP response was fetched before a successful live receipt is
  recorded.

### Gate disposition

- The deterministic two-root harness, full suite, wrapper failure checks, and
  registry/generated-artifact checks passed against the integrated worktree.
- Independent SolUltra re-verification passed the bounded Stage 9 checks,
  including secret/non-network safety in offline modes and target containment.
- The bounded live request succeeded and its exact 22-date coverage,
  backup/restore, scratch correction, and candidate receipt were independently
  verified without repeating the provider request.
- Provider licensing, retention, display, and redistribution rights remain the
  operator's account-specific responsibility and were not independently
  assessed. Operational promotion and old-store retirement remain closed.

## 14. Stage 10 — Bounded FMP market-history candidates

Status: **Retained base and eight-window extension populations complete and
independently re-inspected; private, non-production, and candidate-only.** Exact
receipt pins and the historical evidence qualification are in
[Stage 10 evidence](docs/rebuild/STAGE10_EVIDENCE.md).

### Completed scope

- One frozen 629-symbol roster: current provider-reconciled S&P 500,
  Nasdaq-100, and Dow 30 single names, 95 reviewed non-Russell ETFs, and 15
  reviewed major indexes.
- One original provider-returned full-history request per symbol, followed by
  eight explicit inclusive windows per symbol from `1990-01-01` through
  `2026-08-12`.
- Base result: 621 completed symbols and eight retained failures.
- Extension result: all 5,032 planned windows closed as 3,970 complete, 1,004
  successful empty responses, and 58 authorized terminal outcomes across nine
  tickers.

### Disposition

- Read-only inspection matched 4,235,893 current rows, 4,236,635 immutable
  versions, and zero missing current-version references.
- The accepted repository did not retain a point-in-time record proving that
  independent verification preceded the live calls. This limitation is
  disclosed and is not retroactively reconstructed.
- No Stage 10 provider request may be repeated. Russell 2000 constituents,
  `^RUT`, scheduling, promotion, retirement, public exposure, tools,
  dashboards, Atlas, exports, and destructive operations remain excluded from
  the frozen candidate. IWM remains excluded from that sealed 629-instrument
  receipt, but the separate 2026-08-27 decision added it through the additive
  frozen-95-plus-IWM ETF successor. The one authorized request completed with
  1,254 daily rows and the exact 96-member successor; its private completion
  state prohibits another invocation. This successor did not reopen or repeat
  any completed Stage 10 provider unit. See
  [the additive IWM evidence](docs/rebuild/STAGE10_IWM_EXTENSION_EVIDENCE.md).

## 15. Stage 11 — Bounded BEA/EIA macro candidate

Status: **Private, non-production candidate complete.** The exact state and
receipt evidence are recorded
in [Stage 11 evidence](docs/rebuild/STAGE11_EVIDENCE.md).

### Completed state

- BEA NIPA `T10101/A191RL` and `T10105/A191RC` are published.
- Paginated EIA monthly U.S. all-sector retail sales, revenue, price, and
  customers are published.
- One EIA weekly `PET.WCESTUS1.W` capture is published with 2,289 versioned
  observations and 2,289 current pointers.
- The final resume made zero BEA requests, zero retail requests, and one weekly
  request; it did not repeat either completed phase.
- Targeted integrity, foreign-key, duplicate, and current-pointer checks passed,
  and the private candidate receipt is retained.

### Disposition

- Do not repeat any BEA or EIA request for this candidate.
- Keep broader discovery, scheduling, promotion, retirement, public exposure,
  tools, dashboards, Atlas, exports, backup/restore rehearsal, full-corpus
  reconciliation, and destructive operations closed.

## 16. Stage 12 — Serial Market v1 operationalization

Status: **Stages 12A and 12B implemented and independently verified within
their offline boundaries; Stages 12C and 12D complete and independently
verified; subsequent Stage 12E authority and dated activation evidence are
indexed in the operating envelope.** The focused
[Stage 12C two-session contract](docs/rebuild/STAGE12C_MARKET_GAP_V1.md),
[Stage 12C evidence record](docs/rebuild/STAGE12C_EVIDENCE.md),
[Stage 12D no-transfer contract](docs/rebuild/STAGE12D_PROJECT_LOCAL_OPERATIONALIZATION.md),
[Stage 12D evidence record](docs/rebuild/STAGE12D_EVIDENCE.md),
[Stage 12 Market v1 contract](docs/rebuild/STAGE12_MARKET_V1.md),
[Stage 12B collector contract](docs/rebuild/STAGE12B_INCREMENTAL_MARKET_V1.md),
the immutable [Stage 12A evidence record](docs/rebuild/STAGE12_EVIDENCE.md),
[Stage 12B evidence record](docs/rebuild/STAGE12B_EVIDENCE.md), and
the path decisions [ADR 0009](docs/adr/0009-canonical-market-operational-path.md)
and [ADR 0010](docs/adr/0010-stage12d-no-transfer-market-adoption.md)

### Stage 12A — Authority and coverage freeze

- Designate `data/market.sqlite` as the current project-relative market
  default while preserving exact historical registry projections.
- Freeze the explicit 629-symbol Market v1 roster, asset classifications,
  retained Stage 10 receipt bindings, coverage counts, and non-claims in one
  strict machine-readable manifest.
- Run a dependency-free deterministic gate using explicit temporary roots only.
  It may not open a SQLite database, consult credentials, use the network,
  repeat a provider request, move/copy data, or change a scheduler.

### Stage 12B — incremental collector

Status: **Implemented and independently verified — offline fixture-only.** See
the [focused Stage 12B contract](docs/rebuild/STAGE12B_INCREMENTAL_MARKET_V1.md)
and [Stage 12B evidence](docs/rebuild/STAGE12B_EVIDENCE.md).

- Implement and fixture-test a new bounded collector under registry `2.13.0`,
  schema `1.8.0`; never schedule, reopen, or repeat the sealed Stage 10
  backfill or receipts.
- Use the existing `0010` capture/version/current model only in explicit
  temporary fixture stores. No live provider, API key, environment access,
  network, default or retained store, migration, promotion, cutover, public
  consumer, or scheduler action is permitted.

### Stage 12C — bounded two-session Market v1 gap completion

Status: **Completed and independently verified — bounded manual no-copy
two-session population.** See the [focused contract](docs/rebuild/STAGE12C_MARKET_GAP_V1.md)
and immutable [evidence record](docs/rebuild/STAGE12C_EVIDENCE.md).

- The frozen 629-symbol Stage 12A roster used only `2026-08-13` and
  `2026-08-14`, with `AAPL` as the first complete sentinel and the reviewed
  sorted roster thereafter.
- The existing project-local `data/market.sqlite` initially matched the
  immutable retained Stage 10 baseline at SHA-256
  `b0ee0a02cc74e603320d0fa4f7a68a64339f8ed834229bdc480efa0351cc6f3c`;
  the retained source remains immutable and no second full database copy was made.
- Registry `2.14.0`/schema `1.8.0` adds only
  `market.stage12c.fmp_daily_incremental_manual`; its exact historical
  projection restores `2.13.0` before Stage 12B and earlier evidence.
- The receipt binds all 629 one-attempt chains: 619 published complete, three
  successful-empty noncoverage outcomes, and seven narrowly sealed authorized
  HTTP 402 outcomes. It is not a general HTTP 402 policy; all other HTTP 402
  responses remain systemic.
- The final target has 4,237,131 current rows, 4,237,873 immutable versions,
  and 5,210 captures, with clean integrity, foreign-key, duplicate, and
  current-pointer checks. Final source-neutral checks used
  `mode=ro&immutable=1` after a zero-WAL precondition; the earlier `-shm`
  timestamp side effect is disclosed and is not a database mutation.
- **12D — no-transfer adoption/freeze (complete and independently verified):**
  the [Stage 12D contract](docs/rebuild/STAGE12D_PROJECT_LOCAL_OPERATIONALIZATION.md)
  and [evidence](docs/rebuild/STAGE12D_EVIDENCE.md) record exactly two
  fixed-target read-only proofs, two distinct immutable receipts with one
  semantic digest, exact database/WAL/SHM/journal neutrality, and clean fixed
  checks. The independent reconciliation did not reopen SQLite or compute a
  new full-database hash. No copy, move, replacement, transfer, backup,
  migration, registry bump, promotion pointer, provider/network access, public
  exposure, or scheduler change occurred.
- **12E — separately authorized daily market refresh:** On 2026-08-29 the
  reviewed weekday 18:00 America/New_York host unit was daemon-reloaded,
  enabled, and started. The dated activation evidence and subsequent
  operational decisions are indexed in the
  [operating envelope](docs/rebuild/CURRENT_OPERATING_ENVELOPE.md#recurring-exceptions).
  Each normal clock-driven run snapshots every current FMP
  provider-native Stage 10 `equity`/`etf`/`index` identity before network
  access. The 2026-08-29 preflight contained 630 identities (519 equity, 96
  ETF, and 15 index); the bounded snapshot is dynamic, capped at 800, and
  starts with `AAPL`. The unit requests one current-session daily-OHLCV
  response per symbol, stops the batch on an empty `AAPL` no-market sentinel,
  has no retry or catch-up, preserves per-unit private evidence, writes only
  to `data/market.sqlite`, and writes nothing on exact semantic replay. The
  ten pinned historical noncoverage symbols remain in scope: valid 200 data is
  published if available, only their reviewed empty/HTTP 402 results are
  terminal, and other missing/error outcomes fail closed. It does not replay
  Stage 10/12C history or enable the frozen Stage 7 job. Activation created no
  state directory, provider request, or canonical write.

### Stage 12C exit gate

**Complete.** The offline and independent gates passed before the manual slice.
The initial environment-only credential stop made no provider request or
database mutation; the later separately authorized invocation was not an
automatic retry and completed the frozen plan. The immutable receipt
`0b598c7f5df93cb21104bcf6a45ae3e798a56eda7682474d59b2a81def683f88` binds
the exact scope, attempts, outcomes, sidecars, target checks, and final
integrity/current-pointer results. See
[Stage 12C evidence](docs/rebuild/STAGE12C_EVIDENCE.md).

### Stage 12A exit gate

- Accepted documentation, registry revision `2.12.0`, and executable scope
  agree on `data/market.sqlite` with no fallback to the superseded name.
- The exact roster and retained evidence bindings reject drift, unknown fields,
  secrets, broadened coverage, and opened successor phases.
- Current canonical resolution produces only the selected path, without
  creating it; historical Stage 1–11 projections and evidence hashes remain
  reproducible.
- Focused tests, the complete dependency-free suite, and independent
  verification pass with no database, provider, scheduler, or destructive
  side effect.

### Stage 12B exit gate

- The current `2.13.0` registry and exact `2.12.0` projection reproduce
  their pinned source digests.
- Pure dry runs and two-root fixture rehearsals are deterministic and path-free;
  exact and reordered replay are total no-writes.
- New keys append initial versions, changed keys append strictly later
  corrections, and all failure paths preserve atomicity and point-in-time
  ordering.
- Scope-byte drift, forged scope objects, path aliases, hard links, inode
  replacement before or during locking, malformed/partial data, and resource
  failures fail closed before unintended SQLite access or mutation.
- The complete dependency-free suite and independent verification pass without
  a live provider, credential, default/retained store, migration, cutover,
  public consumer, or scheduler action.

Stage 12A completion did not itself authorize later work. Stage 12B remains
complete only within its offline fixture boundary. A further user decision
authorized Stage 12C, which is now complete under its
[focused contract](docs/rebuild/STAGE12C_MARKET_GAP_V1.md) and immutable
[evidence record](docs/rebuild/STAGE12C_EVIDENCE.md). Its provider work may not
be repeated. Stage 12D is complete and independently verified under its
[no-transfer adoption/freeze contract](docs/rebuild/STAGE12D_PROJECT_LOCAL_OPERATIONALIZATION.md)
and [evidence record](docs/rebuild/STAGE12D_EVIDENCE.md). Stage 12E is
separately authorized; its dated activation and subsequent operational records
are indexed in the operating envelope.

## 17. Cross-stage quality gates

Every implementation change must answer:

1. What stable identity does this introduce or depend on?
2. What evidence proves when the value became available?
3. Is the mutation current-state, versioned, immutable, or tombstone-based?
4. Which physical store owns it?
5. Which registry entry, migration, tool, dashboard view, scheduler job, and
   export depends on it?
6. Can exact replay occur without changing canonical state?
7. Can a temporary-store test prove failure without touching a live path?
8. Can a research result cite exact data, code, parameters, and exclusions?

## 18. Deferred scope

The rebuild does not include:

- order, portfolio, or execution management;
- public or multi-user exposure of the local portal;
- tick-level or full intraday market infrastructure;
- automatic FX conversion or curve interpolation;
- CUSIP-level SOMA holdings;
- silent imputation or hidden composite regime scores;
- Phillips–Perron until a validated numerical dependency is accepted; or
- a provider-rights governance subsystem.

## 19. Roadmap maintenance

- Roadmap stages describe sequence and gates, not detailed contracts.
- Contract changes belong in the relevant specification and ADR first.
- A stage moves to `In progress` only after its dependencies and owned paths are
  explicit.
- A stage moves to `Complete` only when its exit gate has recorded evidence.
- Historical recovery evidence remains in `plan.md`; do not rewrite remembered
  history to make later implementation look inevitable.
