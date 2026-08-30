# Current Operating Envelope

Status: Current operational routing snapshot; non-authorizing
Reconciled: 2026-08-29

## Purpose

Read this document before any provider, credential, network, scheduler,
canonical-store, migration, promotion, retirement, deployment, public-
exposure, or destructive operation.

This envelope consolidates existing user decisions and evidence. It grants no
new execution authority. Code, credentials, registry declarations, unit files,
database files, and historical instructions establish only that artifacts
exist. If an action is not explicitly permitted by the current user request,
stop. A separate focused contract is required only when the
[fast-path](FAST_PATH_DEVELOPMENT.md) heavy-workflow criteria require one;
otherwise the current request plus a stated bounded operational scope is
sufficient authority.

Detailed receipts, hashes, counts, request rules, and historical limitations
remain in the linked contracts and evidence. This file records only the
current control boundary.

## Authority and working-state disclosure

The authority order is the one in the
[rebuild index](README.md): explicit user decisions, accepted ADRs, focused
contracts, the roadmap, and then recovery history.

The current accepted registry is revision `2.63.0`, schema `1.9.0`, with
source SHA-256 `06466e9b79be5bc0fab927a81b5972059bbad34ba4a674c1c456c3eaeaf04d72`.
The active versioned catalog is `2.23.0` at SHA-256
`05cfbfb29b544594a3b176daeca659470f3c91a20a423c730d8da9622c8cae2d`.
Its immediate `2.62.0` predecessor remains byte-identical at registry SHA-256
`59db17edd70d8ae9aa77f1468cf7c459338e3d1dff0015b3c99853b2e1e12fd2` and
catalog `2.22.0` SHA-256 `18e86daf6ef3291407ab794d7f53015c80bb90c88f2518891f7b904002f515a1`.
The former working `2.22.0`/`validated` candidate is rejected under
[ADR 0011](../adr/0011-retire-proposed-bls-cpi-release-archive.md). It never
established provider, canonical-store, consumer, scheduler, or live-population
authority. Because it never entered the active configuration lineage, it
remains absent from the later additive `2.23.0` through `2.63.0` lineage,
including Treasury `2.23.0`, NY Fed headline-rate `2.24.0`, NY Fed
repo-facility `2.25.0`, NY Fed SOMA-summary `2.26.0`,
official macro-conditions `2.27.0`, NY Fed CMDI `2.28.0`, Treasury/EIA/NBER
macro extension `2.29.0`, BLS price/wage/productivity `2.30.0`, and
GDI-vintage `2.31.0` revisions. Revision `2.31.0` adds only additive macro
migration 0017; it adds no scheduler, public surface, dataset ownership,
collector, or credential mechanism. Revision `2.29.0` and the fixed
electricity-retail refresh reuse the existing EIA credential resolver, while
`2.30.0` remains credential-free.

Revision `2.32.0` adds only local read-only public version selection for
`market.get_returns` and `market.get_forward_returns`. The 57 logical names and
omitted-selector v1 behavior remain unchanged; explicit `2.0.0` requests use
the existing Stage 10 typed reader and in-memory return primitive. The change
adds no provider request, credential access, canonical-store write or
inspection, migration, scheduler, Atlas/export connection, hosting, or
deployment. Executable v2 validation uses explicit temporary stores; no v2
request was executed against the default `data/market.sqlite`. Exact
projection restores the prior `2.31.0` registry bytes.

Revision `2.33.0` adds only local, composable `2.0.0` variants of
`timeseries.describe`, `timeseries.align`, and `timeseries.correlation`.
They accept caller-supplied Stage 10 v2 return series, revalidate the full
return and temporal contract, and calculate in memory without opening any
store. The 57 logical names, all omitted-selector behavior, and the frozen v1
catalog remain unchanged. The revision adds no provider request, credential
access, canonical-store read or write, migration, scheduler, Atlas/export
connection, hosting, or deployment. Exact projection restores byte-exact
registry `2.32.0`.

Revision `2.34.0` adds only local, store-free `2.0.0` variants of
`econometrics.regression`, `econometrics.rolling_regression`, and
`econometrics.stationarity` over compatible trailing Stage 10 v2 return
series. It rejects forward and truncated inputs; publishes explicit OLS
inference metadata; uses fixed complete rolling windows; and limits
stationarity to a constant-only, caller-selected fixed-lag ADF test with
MacKinnon 2010 finite-sample critical values and no approximate p-value.
The 57 logical names, omitted-selector behavior, and frozen v1 catalog remain
unchanged. The v2 catalog is `2.2.0` at SHA-256
`5250c18b70066de734cb2a4715ec20825f4a587892ba70728065dde82a4a239c`.
The revision adds no provider request, credential access, canonical-store read
or write, migration, scheduler, Atlas/export connection, hosting, or
deployment. Exact projection restores byte-exact registry `2.33.0`.

Revision `2.35.0` adds one private, manual-only BEA collector for the fixed
monthly NIPA table `T20600`: personal income (`A065RC`), disposable personal
income (`A067RC`), and personal consumption expenditures (`DPCERC`). One
`Year=ALL` request supplies all three series, reuses the existing
`BEA_API_KEY` resolver and generic macro publisher, and retains values as
current-dollar USD millions at seasonally adjusted annual rates. It adds no
migration, dataset, provider family, credential mechanism, job declaration,
public tool, export, or deployment. The existing weekday aggregate wrapper is
the separately authorized recurring exception for this collector. Exact
projection removes only this collector and its three reciprocal dataset
bindings, restoring byte-exact registry `2.34.0` at SHA-256
`9ed2affcaa84c6420c7650c10a02361fad2bd892b33a5df2797e033e300ef86d`.

Revision `2.36.0` adds only explicit, store-free `2.1.0` variants of
`econometrics.regression` and `econometrics.rolling_regression` over
caller-supplied compatible Stage 10 return series. They add HC1, HC3, and
fixed-lag Bartlett Newey-West covariance with asymptotic-normal inference and
fixed Ljung-Box, Koenker-Breusch-Pagan, and Jarque-Bera diagnostics. Existing
`2.0.0` and omitted-v1 behavior is unchanged. The v2 catalog is `2.3.0`
with 20 contracts at SHA-256
`7de5126d50c438d0bb35cb82a9a0dd282251de3acceeb850d69ca20f894ef7b9`.
The revision adds no provider request, credential access, canonical-store read
or write, migration, scheduler, Atlas/export connection, hosting, or
deployment. Exact projection removes only the two `2.1.0` variants and
restores byte-exact registry `2.35.0` at SHA-256
`a5e5b11bd9578428430c96f9eec59214e39f8cbb85f5b74fffc37aebaabdc27e`.

Revision `2.37.0` adds only explicit, store-free `2.1.0` selection
for `econometrics.stationarity`. The unchanged `2.0.0` contract remains
ADF-only. The new variant adds fixed-lag level KPSS with the published 1992
asymptotic critical-value table and an explicit four-state joint ADF/KPSS
interpretation. The v2 catalog is `2.4.0` with 22 contracts at SHA-256
`529d904d46003c53785926730279dc4c37bf09b7d15f99c366122c416c4af26e`.
Exact projection removes only the stationarity `2.1.0` variant and restores
registry `2.36.0` at SHA-256
`5c9702d8ca4c2f896083471cc60adecfd3a43c72d45694d04688ab07e6330035`.

Revision `2.38.0` adds only the store-free `2.0.0`
`econometrics.structural_breaks` policy. It compares pooled and split
classical OLS fits at one caller-declared, zero-based first post-break row and
reports a Chow F test; it does not search for a break or claim multiple-break
inference. Exact finite-sample F inference requires Gaussian, homoskedastic,
independent errors and the standard exogenous fixed-design linear-model
conditions.
The v2 catalog is `2.5.0` with 24 contracts at SHA-256
`e0650e5b76ec9a851220c2395c34f9172f1380b5a5c9b2bc681056c655bdeb5b`.
The revision `2.38.0` registry source SHA-256 is
`3d6c0f31f2c72c20e5459c4e7f2358ea273437d59af99ef017b1f3ad47b1b547`.
Exact projection removes only this policy and restores byte-exact registry
`2.37.0` at SHA-256
`2a2b611ac6f752e6d83a81369155454b1484b8caebf4d1aaa3be60c41f0e866b`.

Revision `2.39.0` adds only an explicit, store-free `3.0.0` variant of
`econometrics.regression`. A request selects exactly one of directional
Engle-Granger cointegration, a fixed-order constant VAR, or a directional
conditional Granger-causality F test. Inputs are two through five mutually
compatible, caller-supplied horizon-one trailing Stage 10 return series; the
Engle-Granger mode requires exactly two. Lag order and direction are explicit,
interior missing rows fail closed, common incomplete edges may be trimmed and
reported, and no lag or model search occurs. Cointegration reports MacKinnon
2010 N=2 finite-sample critical values without a p-value. Granger inference
describes predictive precedence, not structural causality.
The v2 catalog is `2.6.0` with 26 contracts at SHA-256
`2c9424a0ff9cda9130d559c2dacb6cef55ff9ff8537191285219d608411b7f24`.
The revision `2.39.0` registry source SHA-256 is
`f7f445a3dbc991ca7b309ce306e5bc439403d929b96fb9931a22c036cb0eea85`.
Exact projection removes only regression `3.0.0` and restores byte-exact
registry `2.38.0` at SHA-256
`3d6c0f31f2c72c20e5459c4e7f2358ea273437d59af99ef017b1f3ad47b1b547`.

The tool-platform revisions through `2.39.0` add no provider request,
credential access, canonical-store read or write, migration, scheduler,
Atlas/export connection, hosting, or deployment. The frozen v1 catalog remains
byte-identical.

Revision `2.40.0` retains that tool surface and adds the existing private FMP
incremental-calendar migration plus evidence and canonical-event dataset
declarations. Its source SHA-256 is
`72516749f56f962bea2a265ef4a917558ef6e757444ae5d679bc50d2d64e6ed7`.
Exact projection removes only those declarations and restores byte-exact
`2.39.0` at SHA-256
`f7f445a3dbc991ca7b309ce306e5bc439403d929b96fb9931a22c036cb0eea85`.
The declaration grants no authority to run or schedule that collector, access
credentials, or read or write a canonical store.

Revision `2.41.0` implements the user's explicit request for the local
read-only `market.get_price_series` public tool. It exposes the established
Stage 10 reader for a server-resolved ticker and returns coherent
`open`/`high`/`low`/`close` typed series. Inclusive `start_date` and
`end_date` may each be omitted. That revision's manifest has 58 names while the
recovered compatibility target and frozen v1 catalog remain exactly 57 names
and 114 contracts. Its v2 catalog is `2.7.0` with 28 contracts at
SHA-256
`0f89da921b37d210c596646b3c4f47e4dbe27c2fe2579d8772c6db2bed312398`.
The tool performs only host-selected read-only access; it adds no provider
request, credential use, migration, canonical write, scheduler, export,
hosting, or deployment. This implementation and its tests did not open the
default canonical market store.

Revision `2.42.0` implements the user's explicit request for
`market.get_available_ticker` and the supported local subprocess interface
for agents in other projects on this computer. The new tool returns
deterministically ordered typed records only for FMP/provider-native Stage 10
instruments with at least one current daily-price row retrievable through the
existing latest reader. Its empty `{}` arguments select the full bounded set;
optional `limit` is 1 through 10,000. This is current retained-data discovery,
not a live or historical point-in-time universe. The active manifest has 59
names; the frozen 57-name/v1 compatibility surface is unchanged. Catalog
`2.8.0` has 30 contracts at SHA-256
`554873c79f58abbee6f4fbf83e4a6767153c9ff920651825d8cac3bd6075905f`.
The registry source SHA-256 is
`1ab956e8338b864873f25e6e41cc6a18ba9a271a1951bec0bdccf32a07b8fd2b`.
Exact projection removes only this tool and its reciprocal bindings and
restores byte-exact `2.41.0` and catalog `2.7.0`. The CLI exposes only
strict JSON over stdin/stdout and fixes project, registry, and store routing
host-side; it adds no URL, direct SQLite connection, caller path or SQL,
provider request, credential use, write path, migration, scheduler, export,
hosting, or deployment.

Revision `2.43.0` adds one private, manual-only Alpaca collector for one SPY
option-surface cohort. It reuses Stage 10 SPY identity and existing options
tables, so it adds no migration or dataset. The fixed plan makes at most four
single-attempt requests: OPRA calendar, IEX SPY snapshot, paper option
contracts, and the nearest-30-DTE indicative chain inside 23-37 DTE and
80%-120% of spot. It caps the operation at 10,000 rows, 8 MiB, and an enforced
120 seconds. A non-trading date stops after the calendar request; a prior-
session underlying stops before catalog or write, and prior-session contract
quotes become explicit missing rows. Exact semantic replay writes nothing.
Normalization `alpaca_spy_option_surface.v2` retains one-through-nine-digit
source-native underlying and option quote/trade timestamp lexemes for fixed
Inspector use and semantic identity. A transient microsecond-compatible copy
is used only for validation and session-date routing. These fields are not
shared cutoff or `as_of` inputs; capture and availability timestamps retain the
shared canonical microsecond grammar. A valid midpoint excludes the unused
underlying latest trade and its timestamp from canonical material.
The registry source SHA-256 is `841041060550eeb41fed491c19835f77d278cbf68daaa9a7b2f754c3f9c4f0ff`;
exact projection removes only the collector and reciprocal bindings and
restores `2.42.0` at SHA-256 `1ab956e8338b864873f25e6e41cc6a18ba9a271a1951bec0bdccf32a07b8fd2b`.
No public tool, migration, export, deployment, or market-close authority is
added. The current user separately authorized the fixed 15:55 America/New_York
weekday host timer. It was linked and enabled on 2026-08-25 after the focused
offline gate, independent verification, and host status check passed.

Revision `2.44.0` adds one private, manual-only SEC collector for the fixed
AAPL CIK `0000320193`. It makes exactly one submissions request and one
CompanyFacts request, with no retry, and fetches both responses before taking
the physical company-store write lock. A monotonic 120-second pre-write
deadline covers provider capture and parsing. It reuses the existing company SEC and
fundamentals tables, the project credential resolver, and the established
append/version publisher, so it adds no migration, dataset, public tool,
scheduler, export, hosting, or deployment. `SEC_USER_AGENT_NAME` and
`SEC_USER_AGENT_EMAIL` are user-agent components only; their values are never
canonical data or semantic identity. The fixed normalized scope is a reviewed
core metric map for AAPL and deliberately does not claim lossless retention of
every dimensional SEC fact. Exact semantic replay writes nothing. Exact
endpoint response reuses its immutable domain artifact and snapshot when the
other endpoint changes without changing its interpreted membership; a
material CompanyFacts response creates one new
complete run-cohort snapshot under the existing schema. Exact projection
removes only this collector and its reciprocal bindings and
restores registry `2.43.0` at SHA-256
`841041060550eeb41fed491c19835f77d278cbf68daaa9a7b2f754c3f9c4f0ff`.

The user's finite AAPL population request completed on 2026-08-25 with the
locally configured SEC User-Agent components. Exactly two responses totaling
3,953,538 bytes produced one successful run with 3,094 selected source rows
and 6,734 writes: one issuer/version, 1,026 filings and filing memberships,
two immutable complete endpoint artifacts/snapshots, 1,552 fact versions and
memberships, 10 metric definitions/mappings, and 1,552 normalized fundamental
versions. The current Inspector projection returns 682 latest AAPL rows across
the 10 metrics, with retained period ends from 2007-09-29 through 2026-07-17.
The immutable post-check reported integrity `ok`, zero foreign-key or lineage
violations, and no SQLite sidecars. The stable post-read company-store
SHA-256 is
`274737e7f5426a137681e218bf70a4739a36fb5d92a5eac0fa86fdbb574b1c10`.

Revision `2.45.0` adds the private
`sec.company.market_fundamentals` collector and reciprocal bindings to those
same five existing company datasets. It reads only the 519 canonical Stage 10
equities, resolves exact official SEC ticker records, deduplicates shared CIKs,
and then processes one issuer per publisher call. The host wrapper permits one
discovery request plus at most two requests per bounded equity, no retries,
global pacing at no more than five requests per second, a six-hour aggregate
deadline, and per-issuer bounds of 64 MiB, 50,000 selected core rows, and 120
seconds. Failures are isolated by issuer and produce only bounded credential-
free aggregate counts and digests. AAPL delegates to the exact `2.44.0` pilot
identity. The parser preserves the reviewed ten-metric core map and adds
annual 20-F/40-F support. Exact semantic replay writes nothing. Exact
projection removes only the new collector and reciprocal bindings and
restores registry `2.44.0` at SHA-256
`af6545258751f7b7a7e7c68c673e18a36c65762809032db6ea540b33f249c182`.
This revision adds no migration, public tool, export, hosting, or deployment.

The user's current decision explicitly authorizes exactly one bounded
longest-available historical population through this wrapper and scheduled
fetching. The implementation fixes that schedule to a separate hardened,
non-persistent weekday host timer at 07:15 America/New_York. The
registry collector remains `manual_only`; the fixed host unit is the recurring
exception. The 2026-08-25 pass issued 1,011 requests totaling 1,974,931,254
response bytes. Official discovery matched 517 of the 519 equity symbols to
514 issuers, and the pass populated 476 of those issuers. It completed inside
the aggregate deadline with 38 isolated issuer failures, two unmatched
symbols, one submissions-ticker
mismatch, no ambiguous symbols, and no rejected discovery records; it was not
retried. The canonical company store contains 467,858 filings and 571,162
fact/fundamental versions across ten metrics, with selected reference periods
from 2006-12-31 through 2026-08-18. Immutable post-checks reported integrity
`ok`, zero foreign-key or checked lineage violations, and stable Inspector
reads. Its stable SHA-256 is
`9e8765e52ee9595a94294c797d8a0641606622c03c99553fb6552c8e26c764cc`.
After those checks, `quant-data-sec-company-fundamentals.timer` was linked and
enabled without manually starting the service. It is active and waiting for
2026-08-26 07:15 America/New_York; future invocations retain the same
single-attempt/no-retry contract.

Revision `2.46.0` implements the local read-only canonical-access foundation.
It adds explicit `2.0.0` macro search, describe, and series variants while
leaving omitted-version and explicit-v1 behavior unchanged. Official-vintage
IDs never fall back to the generic model. Macro `latest`, `as_of`, and
`first_release` use stored pointer, availability, and first-release evidence;
unsupported first-release requests fail closed. It also adds native Stage 10
volume and macro release-calendar retrieval, with independently optional date
bounds, explicit truncation, and path-free lineage. The active manifest has
61 names. Catalog `2.9.0` has 40 contracts at SHA-256
`e6fa88fa63856247ab00073a14ff1321cfafa05d5323a22c26008e81d0b1eb5c`.
Exact projection removes only the two native tools and three macro v2 policies
and restores byte-exact registry `2.45.0` and catalog `2.8.0`. This increment
performs no provider request or canonical-store operation and adds no
migration, dataset, collector, credential mechanism, write path, scheduler,
export, hosting, or deployment.

Revision `2.47.0` adds only local, store-free `2.0.0` variants of
`data.quality_audit` and `timeseries.transform`, plus the native
distribution-diagnostics, covariance-matrix, deterministic-bootstrap, and
principal-components contracts. They consume caller-supplied typed return
series and perform no provider, credential, canonical-store, migration,
scheduler, export, hosting, or deployment operation. The active manifest has
65 names, 14 version policies, and 18 variants. Catalog `2.10.0` has 52
contracts at SHA-256
`381a78aa59682fbf36cc90acc146d2cfa5b43ea90537b7356eca701df0794fbf`.
Exact projection removes only those two policies and four native names,
restoring byte-exact registry `2.46.0` at SHA-256
`b5236b88a2b320628b870fe3abe7898b76fa5fc1d527a223f87985963e38e264`
and catalog `2.9.0`.

Revision `2.48.0` adds only the explicit, store-free
`market.technical_indicators@2.0.0` successor. It consumes caller-supplied
typed Stage 10 OHLCV values, revalidates their exact instrument, grid,
capture, and point-in-time contracts, and calculates one of 15 deterministic
indicator specifications per call. It opens no store and performs no
provider, credential, canonical-store, migration, scheduler, export, hosting,
or deployment operation. The active manifest remains 65 names and has 15
version policies and 19 variants. Catalog `2.11.0` has 54 contracts at
SHA-256
`864a4d07afbf2558a331d30275cf21f4e31521142ee2d9d010dc26cc5680a757`.
Exact projection removes only this policy and restores byte-exact registry
`2.47.0` at SHA-256
`eefa1288e8007518d466a3d4820522ae113ae6c52dc6df0e448cd3654de4a1b8`
and catalog `2.10.0`.

Revision `2.49.0` adds the private, manual-only
`alpaca.market.etf_option_surface_grid` collector over the existing Stage 10
ETF identities and option relations. Its scope is fixed to `SPY`, `QQQ`,
`IWM`, `DIA`, and `XLB`, `XLC`, `XLE`, `XLF`, `XLI`, `XLK`, `XLP`, `XLRE`,
`XLU`, `XLV`, `XLY`; target DTEs are exactly `1`, `2`, `3`, `7`, `14`, `30`,
`60`, `90`, `180`, and `365`. It selects the nearest positive listed expiry
for each target, breaks ties toward the earlier expiry, collapses targets that
select the same expiry, and requires standard call-and-put listings before an
expiry is eligible. The catalog search is bounded to positive DTEs through
`730`, twice the largest target; if an eligible expiry exists in that window,
every later expiry is strictly farther from every supported target. Contracts
between 80% and 120% of the IEX spot reference are retained with paper-account
indicative snapshots; nonstandard surfaces are explicitly excluded rather
than silently analyzed. Open interest and close observations retain their own
dated present/missing state even when a quote is missing or stale; future-dated
source evidence fails closed. It makes at
most 362 single-attempt requests and is bounded by 900,016 rows, 256 MiB, and
900 seconds. The remaining aggregate byte allowance is passed to each request
before its body is read, and semantic material is framed into a streaming hash
under the same explicit 256 MiB normalized-byte ceiling rather than an implicit
serializer default. Exact semantic replay writes nothing; a partial run
isolates invalid or omitted ETF snapshots, reports its completed and failed
underlyings, and never claims success.

The fixed local Inspector adds the read-only `options-surfaces` view with
allowlisted underlying, target-DTE, expiration, option-type, and surface-state
filters. It exposes explicit state and missing-reason fields for surface,
open-interest, close-price, underlying-quote, rate-curve, dividend-set, and
expiry-model inputs. The existing `spy-options` view and the separately authorized 15:55
SPY host timer remain unchanged and SPY-only. The broad collector has no job,
timer, scheduler, public tool, migration, new dataset, export, hosting, or
deployment scope. Its zero-argument live entry point is
`python3 -m quant_data.operations.alpaca_etf_options_refresh`. On 2026-08-27
the one authorized manual attempt issued 17 requests: one OPRA calendar
request, one shared underlying-snapshot request, and one contract-catalog
request for each of the 15 ETFs. It failed closed as `incomplete_universe`
before any chain request or publication; all underlyings failed and captures,
contracts, surfaces, and writes were zero. It was not retried. See the
[additive IWM evidence](STAGE10_IWM_EXTENSION_EVIDENCE.md). Exact projection
removes only the collector and its
three reciprocal dataset bindings and restores byte-exact registry `2.48.0`
at SHA-256
`3709c16168e2959a946c78e99c50b540b860d5f26ccf4afc3434831b8e9d8524`.
Revision `2.50.0` implements the user's 2026-08-27 decision to add IWM as
an ETF price identity without changing the frozen Stage 10 single-name rule
or historical 629-instrument evidence. The private
`fmp.market.iwm_etf_daily_history` collector derives exactly one
96-member `curated_etfs` successor from the reviewed 95-member snapshot plus
IWM and publishes one complete FMP IWM history through the existing Stage 10
evidence, instrument, universe, and versioned-price relations. It is
`manual_only`, has one request and one attempt, and is bounded by 30,000
rows, 16 MiB, and 45 seconds. Frozen-base and exact-successor checks occur
before credential or provider access; provider work occurs outside the
market-store write lock; publication is atomic and exact semantic replay is a
no-write. The fixed zero-argument operation uses the existing FMP credential
resolver and reserves private attempt state before provider access so a
post-attempt failure cannot silently issue a second request. It adds no
migration, dataset, registry job, timer, tool, dashboard, export, hosting, or
deployment surface. The one authorized live operation completed on
2026-08-27 with 1,254 daily IWM rows from 2021-08-30 through 2026-08-27 and
one complete 96-member successor. Immutable postchecks returned integrity
`ok`, zero foreign-key violations, exact version/current lineage, unchanged
frozen Stage 12C counts, and 1,254 read-only Inspector-visible IWM rows. The
private attempt reservation and completion receipt prohibit another invocation.
See the [additive IWM evidence](STAGE10_IWM_EXTENSION_EVIDENCE.md).
Exact projection removes only the IWM collector and its four reciprocal
dataset bindings and restores byte-exact registry `2.49.0` at SHA-256
`6d34dc495de10de42765e8909e30df744f69ad72d1901259f7da67be2d2710e1`.

Revision `2.51.0` adds only a private, manual-only one-request collector for
the fixed missing IWM interval. Its declaration authorizes no provider call or
canonical write; exact projection restores registry `2.50.0` at SHA-256
`0adc78cbe419b18ece989c9cdd6d918ef13113fa5f573d6f5fbe045b9eca8259`.

Revision `2.52.0` adds explicit `2.0.0` successors for seven analytical
names: point-in-time panels, event studies, signal diagnostics, walk-forward
backtests, robustness suites, multiple-testing correction, and forecast
evaluation. They consume caller-supplied compatible Stage 10 return series and
open no store. Catalog `2.12.0` has SHA-256
`3c15ebcbf145d188681a49016dc621ffc96a48f6480f7ff9067d962c8fa29693`.
Exact projection restores registry `2.51.0`.

Revision `2.53.0` adds the read-only
`company.search_filings@2.0.0` successor for exact-CIK, query-bound cursor
pagination. It removes the legacy 500-row dead end without changing the
frozen v1 default. Catalog `2.13.0` has SHA-256
`b313cc2c4e4fd39311c00a5c18ae3ef8aa4de157f58f23c0837b52a51d601ac5`.
Exact projection restores registry `2.52.0`.

Revision `2.54.0` adds read-only `2.0.0` successors for
`market.search_instruments` and `company.get_share_count_history`. The
former searches retained Stage 10 FMP identities with bound cursor pagination;
the latter exposes only reviewed SEC outstanding, basic weighted-average, and
diluted weighted-average share facts while preserving instant versus weighted
semantics. At that revision the inventory remained 65 logical names, with 25
version policies, 29 variants, and 74 catalog contracts. Catalog `2.14.0` has SHA-256
`a70903e9ba65fd71d5d79775698174dafea630c58c435bb4d8fcc504320aa05b`.
Exact projection restores registry `2.53.0` at SHA-256
`c201524e4e4a72b5377d36390e0cc5c746d392674b598ac1499ab818b418d238`.

Revision `2.55.0` adds only the explicit, store-free
`market.technical_indicators@2.1.0` successor with the causal
`supertrend_ai` calculation over caller-supplied typed OHLC series. The
inventory remains 65 logical names and 25 version policies, with 30 variants
and 76 catalog contracts. Catalog `2.15.0` has SHA-256
`65311bb28efe62651ecb3420f0be13fd413df468487141cca0972d45e7684c85`.
Exact projection removes only v2.1 and restores byte-identical registry
`2.54.0`; no provider, credential, store access, canonical write, or live
operation is introduced.
Revision `2.56.0` adds only the explicit, store-free
`market.technical_indicators@2.2.0` successor with the causal
`swing_structure_forecast` calculation over caller-supplied typed high, low,
and close series. The current inventory remains 65 logical names and 25
version policies, with 31 variants and 78 catalog contracts. Catalog `2.16.0`
has SHA-256
`cb1c6965582b90eaeec27054eda0d66e7a4c603aefa69f9b453ae342d779302b`.
Exact projection removes only v2.2 and restores byte-identical registry
`2.55.0`; no provider, credential, store access, canonical write, or live
operation is introduced.
These revisions add no provider, credential, migration, write, scheduler,
Atlas/export, hosting, or deployment authority.

Revision `2.57.0` adds fifteen explicit local read-only
investment-analysis v2 successors: paginated macro calendar access,
explicit-symbol cross-sectional endpoint returns, retained revision/surprise
readers, direct rates/liquidity/credit/regime vectors, two retained Stage 11
energy readers, and normalized reviewed SEC fact access. They preserve frozen
v1 defaults, add no logical tool name, and retain no portfolio semantics,
opaque composite score, classifier, or investment recommendation. Results are
data-dependent and may be empty or not established under the requested
selection. Inputs and the calendar cursor are bounded; the cursor is query and
cutoff bound.

This revision introduces no provider request, credential access, canonical
write, migration, scheduler, Atlas/export, hosting, deployment, or new
operational authority. Its exact projection restores the previous `2.56.0`
registry and `2.16.0` catalog.

Revision `2.58.0` adds only the explicit, store-free
`market.technical_indicators@2.3.0` KDJ calculation over caller-supplied
typed high, low, and close series. It preserves every predecessor, adds no
logical name, and has no provider, credential, store, write, migration,
scheduler, Atlas/export, hosting, deployment, or operational authority. Its
exact projection removes only v2.3 and restores byte-identical registry
`2.57.0` and catalog `2.17.0`.
Revision `2.59.0` adds five explicit tool variants: Williams Vix Fix v2.4,
market breadth/risk analytics v2.1, fixed-lag seasonality for both retained
energy readers v2.1, and normalized company ratios v2.1. It also corrects the
stationarity and declared structural-break adapters to exclude only expected
incomplete return edges while rejecting interior gaps. The inventory remains
65 logical names with 40 version policies, 52 variants, and 120 contracts.
Its exact projection removes only those five variants and restores
byte-identical registry `2.58.0` and catalog `2.18.0`. It adds no provider,
credential, store-write, migration, scheduler, Atlas/export, hosting,
deployment, portfolio, or investment-recommendation authority.

Revision `2.60.0` adds only the explicit, store-free
`market.technical_indicators@2.5.0` WaveTrend-with-crosses calculation over
caller-supplied typed high, low, and close series. The inventory remains 65
logical names with 40 version policies, 53 variants, and 122 contracts. Its
exact projection removes only v2.5 and restores byte-identical registry
`2.59.0` and catalog `2.19.0`. It adds no provider, credential, store-write,
migration, scheduler, Atlas/export, hosting, deployment, portfolio, or
investment-recommendation authority.

Revision `2.61.0` adds only the explicit, store-free
`market.technical_indicators@2.6.0` Parabolic SAR calculation over
caller-supplied typed high, low, and close series. The inventory remains 65
logical names with 40 version policies, 54 variants, and 124 contracts. Its
exact projection removes only v2.6 and restores byte-identical registry
`2.60.0` and catalog `2.20.0`. It adds no provider, credential, store-write,
migration, scheduler, Atlas/export, hosting, deployment, portfolio, or
investment-recommendation authority.

Revision `2.62.0` adds only the store-free
`market.technical_indicators@2.7.0` rolling-regression-line successor.  It
has 65 logical names, 40 version policies, 55 variants, and 126 contracts;
its exact projection restores byte-identical `2.61.0`/`2.21.0`.

Revision `2.63.0` adds the separate current FMP stock-news successor:
`news:0006_fmp_stock_latest_current`, two private current-news datasets,
manual-only collector `fmp.news.stock_latest_current`, and host-routed
`news.search@2.0.0`.  The manifest remains 65 logical names and now has 41
version policies, 56 variants, and catalog `2.23.0` with 128 contracts.  This
is offline-validated implementation only: no FMP request, canonical
`data/news.sqlite` migration or population, or recurring timer has been
authorized or performed.  The exact projection removes only this successor
and restores byte-identical `2.62.0`/`2.22.0`.



Under [ADR 0012](../adr/0012-compact-fmp-calendar-retention.md), revision
`2.40.0` leaves the complete migration-0016 history untouched and changes only
future wholesale persistence. A material batch adds one immutable receipt and
only new or changed raw event versions, then replaces the single
`fmp_us` latest-response cache; an exact semantic replay writes nothing.
The conservative event identity is provider, country, event time, event name,
and currency. Reschedules are new identities and source absence is not an
authoritative tombstone. The existing collector request limit, FMP credential
resolver, physical lock, and host timer remain unchanged.

On 2026-08-24 the user explicitly authorized applying only migration 0018 to
`data/macro.sqlite`. The reviewed one-store runner applied the exact resource
at `2026-08-25T01:31:39.017401Z`. The immutable post-check confirmed head
`macro:0018_fmp_calendar_incremental_events`, 18 migrations, 24 macro
datasets, four empty new tables, three indexes, twelve triggers, integrity
`ok`, zero foreign-key violations, and unchanged migration-0016 counts of
62 captures and 47,754 rows. The post-migration main-file SHA-256 was
`74a12c867290c95df2b19bea6e6d9834bc5190fc581601cb6a2f6464cb49f898`
at 466,305,024 bytes. Its immutable check left all sidecars absent; the final
publisher-construction read left an ordinary zero-byte WAL and 32-KiB SHM
while preserving that main-file hash. The exact migration gate now passes
before credentials or network. No provider request, credential read,
historical population, scheduler change, or legacy cleanup was performed.

An explicitly authorized 2026-08-21 immutable read-only proof, performed while
registry `2.21.0` was current, confirmed that the canonical macro store
identifies itself as `macro`, ends at the exact accepted ordinal-16 FMP
wholesale migration,
and contains no `0017` ledger collision or BLS original-release archive
relation. Main/WAL/SHM/journal physical stamps were unchanged and all
sidecars were absent. This one-time proof grants no continuing canonical-read
authority. Ignored scratch captures and publisher artifacts remain evidence
that the rejected workflow was exercised; they are not accepted activation or
completion authority.

The current canonical project-relative store declarations are:

| Store | Path |
| --- | --- |
| Market | `data/market.sqlite` |
| Macro | `data/macro.sqlite` |
| Company | `data/company.sqlite` |
| News | `data/news.sqlite` |

A registry path declaration never authorizes opening, creating, migrating, or
writing the corresponding database.

## Bounded manual operational fast path

A current explicit user request may authorize a finite manual provider
workload, use of an existing credential resolver, and writes to an existing
canonical store without creating a new project stage or evidence program.
Before execution, the agent states the resolved provider/dataset, exact store,
date or universe scope, request cap, and retry policy. That single
authorization covers all enumerated units within the stated cap; a separate
approval is not required for each window or symbol.

This lane is available only when the established provider integration,
credential name/resolver, schema, store role/path, locks, and replay-safe
publisher are reused. Responses are bounded and validated before locking;
network work is complete before a write transaction begins; writes are atomic
and use existing relations; and focused preflight plus post-write counts and
integrity checks can establish the result. A private collector binding to
existing datasets is allowed when it adds no migration, dataset ownership,
job, timer, export, public tool, or caller-selected path.

This is task-scoped authority, not a standing provider or store permission. It
does not cover a new provider or credential mechanism, migration, destructive
rewrite, store copy/replacement/promotion/retirement, scheduler, recurring
automation, deployment, public exposure, or an unbounded/materially costly
workload. Those actions retain their applicable heavier gates. It also does
not silently reopen a completed no-repeat population; the current user request
must identify that population if repetition is intended.

## Retired BLS CPI original-release archive candidate

ADR 0011 retires only the proposed `2.22.0` BLS CPI original-release archive:
`macro:0017_bls_cpi_release_archive`, its archive datasets, collector,
handler, and relations. CPI surprises remain FMP-only; archive availability
may not validate, fill, replace, or otherwise affect an output. This does not
retire the completed `2.16.0` BLS annual-revision snapshots, the completed
14-file historical backfill, or the fixed current BLS refresh. No provider,
scheduler, store, or historical-population action follows from the retirement.

## Canonical market boundary

- Stage 12C is complete; that bounded provider write to
  `data/market.sqlite` must not be repeated or broadened.
  See the [contract](STAGE12C_MARKET_GAP_V1.md) and
  [evidence](STAGE12C_EVIDENCE.md).
- On 2026-08-25 the user separately authorized the fixed Alpaca SPY
  option-surface collector and its 15:55 host timer to append new capture
  cohorts to the existing options schema. It is separate from Stage 12E.
- On 2026-08-27 the user separately authorized exactly one fixed FMP IWM
  full-history attempt into the additive 96-ETF successor in
  `data/market.sqlite`. That attempt completed with 1,254 rows and passed
  immutable postchecks. The dependent manual fixed 15-ETF, ten-DTE Alpaca
  attempt then issued 17 requests and failed closed for all underlyings before
  publication, with zero writes. Neither completed attempt may be repeated
  without a new explicit decision; no timer changed and no completed Stage 10
  or Stage 12C provider unit was reopened.
- Stage 12D is complete as a no-transfer, read-only adoption/freeze proof. It
  authorizes no provider, credential, write, copy, backup, promotion, public
  consumer, or scheduler action. See the
  [contract](STAGE12D_PROJECT_LOCAL_OPERATIONALIZATION.md) and
  [evidence](STAGE12D_EVIDENCE.md).
- On 2026-08-29 the reviewed Stage 12E timer was daemon-reloaded, enabled, and
  started. It is `loaded`/`enabled`/`active`/`waiting`, with its first
  next trigger Monday 2026-08-31 18:00:00 EDT and an empty `LastTrigger`.
  The service remains `inactive/dead`, with no start or exit timestamp; no
  state directory, provider request, or canonical write occurred during
  activation. Each normal clock-driven run snapshots every current
  FMP provider-native `stage10_instruments` identity whose `asset_type` is
  `equity`, `etf`, or `index`. The 2026-08-29 preflight contained 630
  identities (519 equities, 96 ETFs, and 15 indexes); the dynamic batch is
  bounded to 800 and ordered `AAPL` first. It then makes one
  current-session FMP daily-OHLCV request per symbol. An empty `AAPL` stops
  the batch as a no-market-session sentinel; the ten pinned historical
  noncoverage symbols remain in scope, valid 200 data is published if
  available, and only their exact reviewed empty/HTTP 402 outcomes are
  terminal. Other missing or error outcomes fail closed. The timer is
  non-persistent, has no retry or catch-up, targets only
  `data/market.sqlite`, retains per-unit private evidence, and writes
  nothing on exact semantic replay. It does not repeat historical Stage 10 or
  Stage 12C work and does not enable the frozen Stage 7 `market-close` job.
- No other default-path market operation is authorized beyond the exact SPY,
  one-attempt IWM/broad-grid, and Stage 12E daily-refresh exceptions above.

## Canonical company boundary

- The fixed two-request AAPL SEC seed completed on 2026-08-25 in
  `data/company.sqlite`; it must not be manually repeated or broadened without
  a new explicit request identifying the finite scope.
- `sec.company.aapl_fundamentals` remains private and manual-only. No company
  timer, recurring job, wider CIK universe, migration, public consumer, or
  default-path company operation is authorized.

## Canonical news boundary

- The frozen `news:0005_fmp_stock_latest` one-shot contract remains historical
  and is not reopened by the current-feed successor.
- The `news:0006_fmp_stock_latest_current` implementation is available only
  for offline validation.  No live provider request, canonical migration,
  canonical population, or recurring timer is authorized or installed.
- `news.search@2.0.0` is a local read-only tool for retained headline metadata
  only.  It is unavailable against the canonical store until migration 0006 is
  separately authorized and applied.  Once the relations exist, no successful
  capture is represented by an honest empty result; raw response bytes and
  article bodies are never exposed through that tool.

## Implemented and populated macro additions

The fixed official-conditions code now supports BLS Employment Cost Index
`CIS1010000000000I`, headline Chicago Fed CFNAI, and FRED `INDPRO` through
existing credential-free provider families and the generic macro publisher.
ECI extends the existing BLS request; CFNAI and INDPRO are separate one-request
sources. The fixed local Inspector exposes them under `Prices, wages &
productivity`, `National activity`, and `Industrial production`.

On 2026-08-24 the user explicitly authorized their historical population and
addition to the existing weekday aggregate. Three non-overlapping BLS requests
published 102 quarterly ECI observations from `2001-Q1` through `2026-Q2`.
One FRED request published 1,291 monthly INDPRO observations from `1919-01`
through `2026-07`. The first CFNAI request failed closed before publication
because the live workbook encodes periods as `YYYY:MM`; one disclosed
recovery request captured the exact workbook, the source-specific parser was
corrected, and that same response published 710 monthly observations from
`1967-03` through `2026-04` without a third CFNAI request. All 2,103
current observations have correction sequence 1 and no missing values.
Immutable post-write checks returned integrity `ok`, zero foreign-key
violations, migration head `macro:0018_fmp_calendar_incremental_events`, and
no WAL, SHM, or journal sidecars. The fixed Inspector returned HTTP 200 and
the exact 102, 710, and 1,291 row totals.

The same decision expands the active macro-current wrapper to twenty-three
operations and a request cap of 31. ECI rides the established one-request BLS
step; full-source CFNAI and INDPRO add one request each through the stable
current-quarter end. No registry revision, migration, credential mechanism,
new unit, timer cadence, public route, or export was added.

## Recurring exceptions

Six recurring scheduler exceptions are recorded as installed and active. The
Stage 12E daily-market timer is enabled and waiting for its first normal
trigger. Normal clock-driven execution is the boundary; agents must not
manually trigger, change, retry, broaden, reinstall, disable, or repurpose any
recurring unit.

| Timer | Fixed scope |
| --- | --- |
| `quant-data-macro-vintages.timer` | 09:05 America/New_York on weekdays. One current BEA GDP/GDI workbook request and one current BLS GDP/CPI request; no retry, migration, credential, or historical-archive fetch. |
| `quant-data-employment-vintages.timer` | First Friday of each month at 10:05 America/New_York. One credential-free BLS request for the fixed payroll and unemployment series; no retry, migration, or Philadelphia Fed historical-workbook fetch. |
| `quant-data-fmp-macro-calendar.timer` | 08:15 and 08:45 America/New_York on weekdays. One bounded current-window FMP calendar request with no retry. The response is retained as wholesale raw evidence before independent GDP/CPI and employment normalization. |
| `quant-data-macro-current-refresh.timer` | 18:30 America/New_York on weekdays. Twenty-three established macro collector operations run sequentially with a total provider-request cap of 31, no retry, a fixed `data/macro.sqlite` target, and semantic no-write behavior when content is unchanged. It covers Treasury curve; NY Fed overnight rates including SOFR distribution, volume, index, and compounded averages; Federal Reserve IORB, target bounds, H.4.1, and INDPRO; repo facilities and SOMA; Chicago Fed NFCI/ANFCI components and CFNAI; BIS credit conditions; CMDI; Treasury cash, Debt to the Penny, and Monthly Treasury Statement receipts, outlays, and deficit/surplus; EIA gas storage, weekly crude-oil stocks, weekly gasoline and distillate stocks, finished-gasoline product supplied, and monthly electricity retail; NBER recession chronology; BLS PPI, earnings, productivity, and ECI; and BEA personal income, disposable personal income, and personal consumption expenditures. |
| `quant-data-alpaca-spy-options.timer` | 15:55 America/New_York on weekdays, with an in-process OPRA trading-calendar gate. It targets only `data/market.sqlite`, makes at most four single-attempt requests, enforces 10,000 rows, 8 MiB, and 120 seconds, uses the paper account and indicative option feed with an IEX SPY spot reference, and writes nothing on exact semantic replay. |
| `quant-data-market-close.timer` | Enabled and active/waiting. On 2026-08-29 it was daemon-reloaded, enabled, and started; `LastTrigger` is empty, the service is `inactive/dead` with no execution timestamps, and activation made no state directory, provider request, or canonical write. Its next trigger is Monday 2026-08-31 18:00:00 EDT. On weekdays at 18:00 America/New_York, it snapshots every current FMP provider-native Stage 10 `equity`/`etf`/`index` identity. The 2026-08-29 preflight contained 630 (519 equity, 96 ETF, and 15 index); the dynamic batch is bounded to 800 and begins with `AAPL`. It makes one current-session daily-OHLCV request per symbol. An empty `AAPL` stops the batch. Pinned historical noncoverage symbols remain eligible; only their reviewed empty/HTTP 402 outcomes are terminal and other errors fail closed. It is non-persistent, has no retry or catch-up, targets only `data/market.sqlite`, retains per-unit private evidence, and writes nothing on exact semantic replay. |

These exceptions do not enable any recovered Stage 7 job, provider or series
beyond the exact scope above, a different cadence, catch-up run, or historical
backfill. The aggregate current refresh and the active Stage 12E timer leave
the underlying registry collectors manual-only and are separate fixed
host-level exceptions. See
[scheduling and locking](SCHEDULING_AND_LOCKING.md) and the read-only
[unit inspection guide](../../deploy/systemd/README.md).

## Completed work that must not be repeated

The following are completed, retained operations, not standing permissions:

- the bounded Stage 9 FMP SPY slice
  ([evidence](STAGE9_EVIDENCE.md));
- the Stage 10 base population and historical extension
  ([evidence](STAGE10_EVIDENCE.md));
- the Stage 11 BEA/EIA macro population
  ([evidence](STAGE11_EVIDENCE.md));
- the Stage 12C two-session market population
  ([evidence](STAGE12C_EVIDENCE.md));
- the one-time GDP/CPI historical archive and initial official-vintage
  population;
- the one-time Philadelphia Fed employment historical-workbook population;
- the one-time macro-history extension and local Stage 11 weekly adoption;
- the one-time FMP GDP/CPI release-calendar history;
- the one-time FMP wholesale calendar history and its local replay;
- the one-time FMP Treasury curve population;
- the one-request New York Fed headline-rate population for the inclusive
  requested window `2016-03-01` through `2026-08-21`, published to
  `data/macro.sqlite` on 2026-08-21. It retained 11,549 current observations:
  2,632 EFFR, 2,632 OBFR, and 2,095 each for TGCR, BGCR, and SOFR. The provider
  returned observations through `2026-08-20`; integrity and foreign-key checks
  passed, and the fixed local Inspector read returned EFFR `3.63` for that
  date. On 2026-08-23, two separately authorized no-retry requests over the
  same fixed window added four SOFR percentiles and SOFR volume with 2,095
  observations each from `2018-04-02` through `2026-08-20`, plus the SOFR
  index and 30/90/180-day compounded averages with 1,618 observations each
  from `2020-03-02` through `2026-08-21`;
- the one-request New York Fed repo-facility population for the inclusive
  requested window `2016-03-01` through `2026-08-21`, published to
  `data/macro.sqlite` on 2026-08-22. It retained 3,882 current observations:
  2,618 ON RRP rows from `2016-03-01` through `2026-08-21`, and 1,264 SRF
  rows from the facility start `2021-07-29` through `2026-08-21`. The latest
  accepted amounts were USD 200,000,000 for ON RRP and USD 0 for SRF. Integrity
  and foreign-key checks passed, and the fixed local Inspector returned both
  series under the `Repo facilities` view at registry `2.26.0`; and
- the one-request New York Fed aggregate SOMA-summary population for the
  inclusive requested window `2003-07-09` through `2026-08-21`, published
  to `data/macro.sqlite` on 2026-08-22. The provider returned 1,207 weekly
  summaries through `2026-08-19`; all nine declared aggregate components per
  week were retained as 10,863 component rows in the existing SOMA relations,
  with source-empty values represented explicitly and amounts stored in
  thousands of USD. Integrity and foreign-key checks passed, and the fixed
  local Inspector returned 1,207 latest total-holdings rows under the
  `SOMA summary` view at registry `2.26.0`;
- the one-request Federal Reserve H.4.1 population for the requested window
  `2002-12-18` through `2026-08-22`, published to `data/macro.sqlite` on
  2026-08-22 from one FRED ZIP carrying the underlying Board series. It
  retained 1,236 weekly observations each for total assets, reserve balances,
  and the Treasury General Account through `2026-08-19` (3,708 total);
- the one-request Federal Reserve policy-rate population for `2008-12-16`
  through `2026-08-23`, published to `data/macro.sqlite` on 2026-08-23 from
  one credential-free FRED CSV. It retained 6,460 daily rows for each of IORB,
  the target-range lower bound, and the target-range upper bound (19,380
  observation versions total). The 4,608 pre-IORB dates remain explicit
  source-missing observations; the latest values are 3.65%, 3.50%, and 3.75%.
  Immutable read-only integrity and foreign-key checks passed, and the fixed
  Inspector view is
  `Policy rates`;
- the one-request Chicago Fed population for the requested window
  `1971-01-08` through `2026-08-22`, published on 2026-08-22. It retained
  2,902 weekly observations each for NFCI and ANFCI through `2026-08-14`
  (5,804 total). One later authorized same-response refresh on 2026-08-23
  added 2,902 observations each for the NFCI risk, credit, and leverage
  components over the same coverage, bringing the five-series checkpoint to
  14,510 observations; and
- the two-request BIS U.S. private non-financial credit population for the
  requested window `1961-Q1` through `2026-Q2`, published on 2026-08-22.
  Credit-to-GDP and its gap each retained 260 quarters from `1961-Q1` through
  `2025-Q4`; debt-service ratio retained 108 quarters from `1999-Q1`
  through `2025-Q4` (628 total). Immutable read-only integrity checks passed
  after all three `2.27.0` populations, and the fixed Inspector views are
  `Fed H.4.1 liquidity`, `Financial conditions`, and `BIS credit conditions`.
- the one-request NY Fed CMDI population for the requested window
  `2005-01-07` through `2026-08-22`, published on 2026-08-22 from the
  fixed official workbook. It retained 1,125 weekly observations each for
  the overall market, investment-grade, and high-yield indexes through
  `2026-07-24` (3,375 total). Immutable read-only integrity and foreign-key
  checks passed, and the restarted local Inspector returns all 1,125
  overall-market rows under `Corporate bond distress` at registry `2.28.0`;
- the one-request Treasury Fiscal Data TGA population for the requested window
  `2005-10-01` through `2026-08-22`, published on 2026-08-22. The provider
  returned and the canonical store retained 1,089 daily observations from
  `2022-04-18` through `2026-08-20`;
- the two-request Treasury Fiscal Data Debt to the Penny population for the
  non-overlapping windows `1993-04-01` through `2009-12-31` and `2010-01-01`
  through `2026-08-23`, published on 2026-08-23. An initial first-window
  attempt was rejected before publication; one bounded diagnostic established
  that Treasury represents unavailable early component values as the literal
  string `null`, now retained as explicit source-missing observations. The
  canonical store contains 8,376 dates for each of total public debt, debt
  held by the public, and intragovernmental holdings (25,128 observations)
  through the provider's latest `2026-08-20` record. Total debt begins
  `1993-04-01` with no missing values; the two components each have 2,958
  source-missing rows before their first reported value on `1997-09-30`.
  Latest values are USD 40,033,256,786,764.37, USD 32,278,964,736,853.40,
  and USD 7,754,292,049,910.97 respectively. Immutable read-only integrity,
  foreign-key, fingerprint, and sidecar checks passed, and the fixed Inspector
  view is `Treasury debt`;
- the one successful no-retry Monthly Treasury Statement population for the
  requested window `1980-10-01` through `2026-08-23`, published on
  2026-08-23. Treasury returned 550 monthly observations for each of federal
  receipts, federal outlays, and deficit/surplus from `1980-10` through
  `2026-07` (1,650 current observations total), with no missing values. The
  latest values are USD 334,010 million of receipts, USD 766,318 million of
  outlays, and a USD 432,308 million deficit; the source convention retains a
  positive deficit and negative surplus without sign inversion. The first
  population request failed closed before any database write because of an
  unsupported secondary sort field; one one-row diagnostic identified it,
  and the corrected request succeeded without retry. Immutable read-only
  integrity, foreign-key, fingerprint, and sidecar checks passed, and the
  fixed Inspector view is `Federal fiscal balance`;
- the one-request EIA Lower-48 working natural-gas-storage population,
  published on 2026-08-22 through the existing EIA credential resolver. It
  retained 868 weekly observations from `2010-01-01` through `2026-08-14`;
- the one-request current EIA weekly crude-oil-stock refresh published on
  2026-08-23 through the existing EIA credential resolver. It extended the
  locally adopted Stage 11 series from 2,289 observations through
  `2026-08-07` to 2,290 observations through `2026-08-14`. Integrity and
  foreign-key checks passed, and the fixed Inspector returns the series under
  `Crude oil stocks`;
- three separately authorized one-request EIA petroleum populations published
  on 2026-08-23 through the existing EIA credential resolver: 1,911 weekly
  total-motor-gasoline stock observations from `1990-01-05` through
  `2026-08-14`, 2,290 weekly distillate-fuel-oil stock observations from
  `1982-08-20` through `2026-08-14`, and 1,854 weekly finished-motor-gasoline
  product-supplied observations from `1991-02-08` through `2026-08-14`.
  All three have no missing current values and correction sequence 1. Latest
  values are 209,378 thousand barrels, 105,619 thousand barrels, and 8,689
  thousand barrels per day respectively. Immutable integrity, foreign-key,
  fingerprint, and sidecar checks passed, and the fixed Inspector view is
  `Petroleum fundamentals`; and
- the one-request NBER business-cycle chronology population, published on
  2026-08-22 from the fixed official JSON. It retained 2,061 derived monthly
  recession-indicator observations from `1854-12` through `2026-08`.
  Immutable read-only integrity and foreign-key checks passed after all three
  `2.29.0` populations, and the fixed Inspector views return 1,089 Treasury,
  868 natural-gas, and 2,061 recession rows; and
- the credential-free BLS price/wage/productivity population. Its initial
  fixed 2017 through 2026 API request, published on 2026-08-22, retained 115
  monthly PPI, 115 monthly average-hourly-earnings, and 38 quarterly
  labor-productivity observations. Five later authorized, non-overlapping
  productivity-only requests for 1947-1956 through 1987-1996 added 199 rows.
  An earlier mixed 1997-2006 request was rejected before publication. On
  2026-08-23 a bounded three-attempt, no-retry extension first rejected a
  PPI-only 2007-2016 request before publication, then published 182 PPI and
  earnings versions from one 2009-2016 request and 34 earnings versions from
  one 2006-2008 request. The immutable checkpoint is now 201 PPI observations
  from source-native `2009-11` through `2026-07`, 245 earnings observations
  from source-native `2006-03` through `2026-07`, and 237 productivity
  observations from `1947-Q2` through `2026-Q2` (683 total). Integrity,
  foreign-key, sidecar, and fixed Inspector checks passed. The source provides no PPI observation before 2009-11 and no earnings
  observation before 2006-03. The successful
  initial and historical windows must not be repeated; any different scope
  requires new finite authorization; and
- the ECI, CFNAI, and INDPRO populations recorded above. Their successful
  three BLS windows, one FRED response, and captured CFNAI recovery response
  must not be repeated manually; the active weekday aggregate may revalidate
  only its fixed current/full-source scopes; and
- the one-request current BEA GDP/GDI workbook refresh published on
  2026-08-23. It added 97 current real and 97 current nominal GDI quarters
  from `2002Q1` through `2026Q1`, with 916 real and 933 nominal retained
  vintage versions. The paired BLS response was replayed without adding an
  observation version. Integrity and foreign-key checks passed, and the fixed
  Inspector returns both GDI series at registry `2.31.0`; and
- the one-request BEA NIPA `T20600` personal-income/outlays population,
  published on 2026-08-24. It retained 810 monthly observations each for
  personal income, disposable personal income, and personal consumption
  expenditures from `1959-01` through `2026-06` (2,430 total), with no
  missing current values and correction sequence 1. The latest values are
  USD 26,994,002 million, USD 23,722,574 million, and USD 22,184,132 million,
  respectively, at seasonally adjusted annual rates. The complete validated
  snapshot has 2,430 memberships; immutable integrity, foreign-key,
  fingerprint, sidecar, and Inspector checks passed. The fixed Inspector view
  is `Personal income & outlays`.

The weekday aggregate current-refresh exception above may revalidate its fixed
current or source-native full-response scopes after these initial populations.
That narrow recurring no-retry path does not authorize a manual repeat,
historical extension, catch-up run, different range, provider change, or
additional series.

The rebuild [index](README.md), root [project record](../../README.md), and
stage evidence own the exact scope, receipt, waiver, hash, count, and historical
projection details. None of the completed work authorizes a repeat, new target,
new date range, provider change, retry, promotion, retirement, public exposure,
or scheduler beyond the four fixed exceptions above.

## Closed operations

Outside a current bounded operational authorization or an applicable accepted
heavier gate:

- do not make a live provider request outside the four clock-driven
  exceptions above;
- do not read an existing credential or introduce a credential mechanism for
  an unapproved operation;
- do not install, start, enable, disable, update, or remove a scheduler;
- do not open or write an existing canonical store, or create, migrate, copy,
  move, back up, replace, promote, retire, or destructively inspect one;
- do not expose a public tool, dashboard, export, route, or caller-selected
  database path;
- do not host or deploy Atlas, connect it to operational stores, or run live
  diagnostics;
- do not repeat a completed historical population or locally adopted cohort;
  and
- do not infer authority from a declaration, test, unit file, executable,
  database, environment variable, or historical instruction.

Read-only canonical checks are allowed when the bounded operational scope or
applicable accepted procedure makes them in scope. They must use the
established immutable/fingerprint-preserving procedure; an ordinary SQLite
open can create or update sidecars and is not presumed mutation-free.

## Required document routing

| Work | Additional authority |
| --- | --- |
| Private collector binding to existing datasets | [Fast path](FAST_PATH_DEVELOPMENT.md), [registry specification](SYSTEM_REGISTRY_SPEC.md), and explicit current scope |
| Migration, registry schema/dataset ownership, or shared identity | [Registry specification](SYSTEM_REGISTRY_SPEC.md), [migration reconstruction map](MIGRATION_RECONSTRUCTION.md), applicable ADR, and explicit current scope |
| Provider, credential, canonical path, or lock | [Fast path](FAST_PATH_DEVELOPMENT.md), [scheduling and locking](SCHEDULING_AND_LOCKING.md), and an applicable collector/stage contract only when the heavy-workflow criteria require one |
| Timer or scheduler | [Scheduling and locking](SCHEDULING_AND_LOCKING.md), explicit current authority, and the applicable accepted contract |
| Identity, availability, as-of, or missingness | [Data and time contracts](DATA_AND_TIME_CONTRACTS.md) and `ARCHITECTURE.md` |
| Market default | Stage 12C/12D contracts and evidence linked above |
| Fixed local read-only tool or Inspector | [Fast path](FAST_PATH_DEVELOPMENT.md) and [tool platform](TOOL_PLATFORM_SPEC.md) |
| Writable UI, export, Atlas, deployment, or public route | [Tool platform](TOOL_PLATFORM_SPEC.md), applicable UI/export contract, and explicit public/deployment authority |
| Destructive, promotion, retirement, copy, or backup action | Exact target, recovery evidence, applicable contract, and a new explicit user decision |

If these sources conflict, do not select the permissive interpretation. Stop,
identify the exact conflict, and request the smallest decision needed.
