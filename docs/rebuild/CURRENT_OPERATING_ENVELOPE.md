# Current Operating Envelope

Status: Current operational routing snapshot; non-authorizing
Navigation reconciled: 2026-09-29; recorded decisions remain dated evidence,
not a live host inspection. The original baseline was reconciled 2026-09-05.

## Purpose

Read this document before any provider, credential, network, scheduler,
canonical-store, migration, promotion, retirement, deployment, public-
exposure, or destructive operation.

This envelope consolidates existing user decisions and evidence. It grants no
new execution authority. It is the sole index of recorded operational
authorization and dated activation evidence; scheduling mechanics live in
[SCHEDULING_AND_LOCKING.md](SCHEDULING_AND_LOCKING.md). Other documents link here
instead of maintaining competing status inventories. Activation, waiting-state,
first-trigger, and receipt observations describe their recorded dates, not
current host health. This reconciliation performed no live status check.

Code, credentials, registry declarations, unit files, database files, and
historical instructions establish only that artifacts exist. Agent-initiated
operations require explicit authority for their finite scope; normal clock
execution of the listed recurring exceptions retains its separately recorded
authority. Stop only an affected unauthorized or unresolved action. A separate
focused contract is required only when the
[fast-path](FAST_PATH_DEVELOPMENT.md) heavy-workflow criteria require one;
otherwise the current request plus a stated bounded operational scope is
sufficient authority.

Detailed receipts, hashes, counts, request rules, and historical limitations
remain in the linked contracts and evidence. This file records only the
current control boundary.

For recurring work, start with the [latest decision links](#recurring-exceptions).
They route around superseded baseline scopes without rewriting their evidence.

## Authority and working-state disclosure

The authority order is the one in the
[rebuild index](README.md): explicit user decisions, accepted ADRs, architecture
and focused contracts, the roadmap, and then recovery history. Resolve clear
supersession under that order and note it briefly. Preserve historical records
as evidence of their original scope; a later explicit decision does not rewrite
an earlier proof. Unresolved authority blocks only the affected action.

The current registry is defined by [the checked-in configuration](../../config/system_registry.json);
public contracts are discoverable through the [local agent guide](../LOCAL_AGENT_TOOLS.md#cross-project-quick-start).
The following hashes and counts describe the September 5 historical baseline.

At that baseline, the registry was revision `2.70.0`, schema `1.9.0`, with
source SHA-256 `4c2de9ef1ac49a4c23ab326000878fa66629caa1f8a0bcb65d4c089d827e9ac3`.
The versioned catalog at that baseline was `2.27.0` at SHA-256
`6f143f9f32fe0cc7d713b9afb1425901ee96e3fdea1539d09f8d602ca794894b`.
The additive local ETF snapshot preserves exact registry `2.69.0` at
SHA-256 `2e9c3e4d2bfc263735a1e9c875d2091210065e0a375a0a0e0c420839a03c774f`
and catalog `2.26.0` at
`fcfb29de2c2138995918e40c603704a0b2df4c17b6c3229312734bb46b0f2a28`.
Its one user-requested read at decision cutoff `2026-09-05T00:00:00Z`
returned all 25 symbols with blocked feature readiness, missing August month-end
observations and unestablished split-only provenance. Store and sidecar stamps
were unchanged; no provider request or scheduler action occurred.
See the [ETF interface and gap report](../LOCAL_AGENT_TOOLS.md#etf-allocator-snapshot).
Registry `2.69.0` has the immediate `2.68.0` predecessor retained at SHA-256
`9b59f6b643e4cff7390559763c8532215ac9927a1f3119870385127af3a6a27e`. The earlier `2.67.0` predecessor is retained at registry SHA-256
`a80b0e06db95968c9fd49cd3d90054b709c57895993a28b512ba2550e162f325`; the public catalog remains byte-identical.
Registry `2.66.0` remains byte-identical at registry SHA-256
`f7b8c402ce4abce5d024f7fcdc8debde97e25324739f037ef209312bb4d070f3`
and catalog `2.25.0` SHA-256
`e35b136e3e47a6211a85d62c75baf6ecd52b9246938a30549ace5c19e0c39700`.
Registry `2.65.0` remains byte-identical at registry SHA-256
`c22d9ada8be3c3c7f9538c902bac3ef3467b9fdfa43c23fd7aa1c88200d58614`
and catalog `2.24.0` SHA-256
`6a4f7e8ce223658617512928b860f5cf5bde85e01f075070771fa019e882ed46`.
Registry `2.64.0` remains byte-identical at registry SHA-256
`b47b6ad63ecaa41477388af033c7f928083ceb5e7db17bf76ff4ab99f71f3dc4`
with the same `2.24.0` catalog. Registry `2.63.0` remains byte-identical at SHA-256
`06466e9b79be5bc0fab927a81b5972059bbad34ba4a674c1c456c3eaeaf04d72` and
catalog `2.23.0` SHA-256 `05cfbfb29b544594a3b176daeca659470f3c91a20a423c730d8da9622c8cae2d`.
The former working `2.22.0`/`validated` candidate is rejected under
[ADR 0011](../adr/0011-retire-proposed-bls-cpi-release-archive.md). It never
established provider, canonical-store, consumer, scheduler, or live-population
authority. Because it never entered the active configuration lineage, it
remains absent from the later additive `2.23.0` through `2.68.0` lineage,
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
Normalization `alpaca_spy_option_surface.v3` retains one-through-nine-digit
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
enabled without manually starting the service. That check recorded active and
waiting for 2026-08-26 07:15 America/New_York; future invocations retain the same
single-attempt/no-retry contract.

On 2026-09-02 the generic SEC publisher first advanced to
`sec_companyfacts.1.2.0`; its same-form/date enrichment rule proved too
narrow for normal CompanyFacts/submissions endpoint disagreement. Version
`sec_companyfacts.1.3.0` allowed a later submissions snapshot to add
membership for the same issuer and accession when the stored row had the exact
date-only, null-report-period, unavailable-document, base-URL CompanyFacts
placeholder shape. One explicitly authorized v1.3 pass at
2026-09-03T04:16Z--04:24Z made 1,027 requests: 397 issuer inputs were unchanged
and the same 116 inputs still conflicted atomically. It made zero canonical
output-data writes and retained 116 v1.3 failure-ledger rows. Immutable
post-checks returned `quick_check=ok` and zero foreign-key violations; v1.3
therefore did not establish a complete repair.

Version `sec_companyfacts.1.4.0` extended only that same full structural
placeholder rule to later CompanyFacts fallback snapshots as well as
submissions snapshots. Its scheduled 2026-09-03 pass made 1,027 requests
across 513 issuers: 315 succeeded and 198 conflicted, so v1.4 did not establish
a repair.

Version `sec_companyfacts.1.5.0` extends preservation to a generic accession
already first-observed for the same issuer, including an ordinary submissions
row whose official metadata is later revised or represented differently by
CompanyFacts. It retains the immutable first-observed filing and appends only
the new snapshot membership. Accessions first observed for another issuer and
the legacy AAPL path retain their existing strict checks. The
`sec_companyfacts_run_v1_5` identity permits failed v1.4 inputs to reach this
path once.

One explicitly authorized v1.5 pass ran from 2026-09-03 21:35 to 22:18 EDT.
It made 1,027 requests across the same 513 issuers: 512 succeeded and one,
NextEra Energy, retained an isolated conflict; three of the 519 roster symbols
remained unmatched. Of the successes, 246 new v1.5 ingestion runs committed
404,278 writes and the others were semantic no-ops. Immutable post-checks
returned `quick_check=ok` and zero foreign-key violations. The run exited
nonzero because it was incomplete and was not retried. Version v1.5 changes no
migration, provider, credential, timer, roster, or store path.

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

Revision `2.65.0` corrects both Alpaca option collectors to treat one explicit
delivery of 100 shares of the same underlying at 100% allocation as standard;
the ETF normalization is now `alpaca_etf_option_surface_grid.v2`. True adjusted
contracts remain excluded from analytic surfaces. New private relations retain
each exact JSON response body by content hash and link every response to its
capture before the normalized surface rows are completed, so exclusion no
longer erases received price evidence. Migration
`market:0011_option_raw_evidence` corrects only stored Alpaca contracts whose
explicit delivery also matches their canonical underlying. Previously
discarded quotes cannot be recovered; affected historical surfaces receive an
explicit `source_quote_not_retained_before_raw_option_storage` missing reason
rather than a fabricated price. This revision changes no credential resolver,
provider scope, request bound, timer, scheduler, public tool, dashboard,
export, hosting, or deployment authority. Exact raw-and-normalized replay still
writes nothing; a byte-distinct response is retained as new evidence even when
its normalized numeric values are equivalent.

At `2026-08-31T01:42:58.896154Z` (2026-08-30 local time), the verified
forward migration was applied to the fixed `data/market.sqlite` under the
physical store lock without a provider request or scheduler action. The
immutable post-check found migration head 0011 at registry `2.65.0`, all 294
affected Alpaca contracts classified standard, and all 1,162 pre-storage
surfaces/open-interest/close observations carrying the explicit legacy
missing reasons. Both raw relations are registered and contain zero rows until
a future capture supplies response bytes; all four restored option update
guards and all four new raw-evidence immutability guards are present.

Revision `2.66.0` adds only bounded local read-only news/research tools and
`news.search@2.2.0` cursor pagination over already retained data. It introduces
no provider, credential, migration, canonical write, scheduler, export,
hosting, or deployment authority. Exact-match story clusters are candidates;
provider symbols are not silently promoted to canonical entity identities;
event and sentiment labels are deterministic derived annotations; and event
impact is retrospective raw close-to-close performance with explicit temporal
limitations, not a causal or abnormal-return claim. Exact projection restores
byte-identical registry `2.65.0` and catalog `2.24.0`.

Revision `2.67.0` adds only local read-only Data Status and options-access
contracts over already retained state. `data.get_dataset_status@1.0.0`
evaluates retained successful-capture anchors against declared thresholds and
does not probe a provider, credential, scheduler process, or network. The three
options v2 successors read the fixed paper/indicative Alpaca ETF cohort without
mixing captures; raw responses remain private and missing/nonstandard states
remain explicit. The Inspector `/healthz` route is process liveness only. This
revision adds no provider execution, canonical write, migration, scheduler,
export, deployment, or public-network authority. Exact projection restores
byte-identical registry `2.66.0` and catalog `2.25.0`.

Revision `2.68.0` adds six private, credential-free, bounded `manual_only`
macro-history collectors for CFTC TFF and disaggregated futures-only history,
Treasury securities auctions, NY Fed Primary Dealer Statistics, and Federal
Reserve H.8 and SLOOS. They bind only to existing
`fixture.macro.rtdsm_employ_evidence`, `fixture.macro.rtdsm_employ`, and
`fixture.macro.stage3_catalog`, derive locks from those outputs, and have one
attempt with no retry. H.8 uses seven fixed singleton requests and SLOOS uses
six, each under one 16 MiB aggregate response cap. This declaration adds no migration, job, scheduler, public
tool, dashboard, export, catalog, or provider-execution authority. Exact
projection restores byte-identical registry `2.67.0` at SHA-256
`a80b0e06db95968c9fd49cd3d90054b709c57895993a28b512ba2550e162f325`; catalog `2.26.0` remains unchanged. On 2026-09-04, a finite parser-only
gate made exactly 13 no-retry singleton attempts for H.8 and SLOOS; every
attempt failed before an HTTP response and no body was accepted, so neither
source entered the recurring macro-current scope.

The fixed local Inspector adds the read-only `options-surfaces` view with
allowlisted underlying, target-DTE, expiration, option-type, and surface-state
filters. It exposes explicit state and missing-reason fields for surface,
open-interest, close-price, underlying-quote, rate-curve, dividend-set, and
expiry-model inputs. The existing `spy-options` view remains available. On
2026-08-30 the user separately authorized updating the enabled, legacy-named
`quant-data-alpaca-spy-options.timer` in place to invoke the fixed 15-ETF
collector at its then-current 15:55 weekday cadence. The collector still has no
registry job or public tool; its `manual_only` declaration is preserved and
the fixed host unit is the explicit recurring exception. On 2026-08-31 the user
authorized moving the same fixed timer to 16:20 America/New_York so collection
begins after the latest ordinary ETF-option session. The focused unit test and
`systemd-analyze --user verify` passed. The daemon reload made no provider
request or canonical write, but restarting the active timer unexpectedly
dispatched the already-passed same-day event at 20:29 EDT despite
`Persistent=false`. That unplanned incomplete-universe run issued 64 requests,
retained 54 captures, materialized 8,812 surface rows, and made 27,031 writes.
It completed SPY, QQQ, IWM, DIA, XLB, and XLC; the other nine fixed underlyings
failed. It exited 75, was not retried, and its immutable captures were not
deleted. After completion the failure flag was cleared without another service
start. The service is `inactive/dead`, retaining its 20:29:50-20:30:17 EDT
timestamps and exit status 75. The timer is
`loaded`/`enabled`/`active`/`waiting` for Tuesday 2026-09-01 16:20 EDT.
Its zero-argument live entry point is
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
migration 0006, two private datasets, manual-only collector
`fmp.news.stock_latest_current`, and host-routed `news.search@2.0.0`. The
manifest remains 65 logical names with catalog `2.23.0`. The bounded
2026-08-30 proof applied migration 0006 and retained 229 articles from one
request without retry. Exact projection restores byte-identical
`2.62.0`/`2.22.0`.

Revision `2.64.0` adds migration `news:0007_current_multi_source` (SHA-256
`df58936c73045ba8a382bf0a24743db5e314246e0f81d8943872b6d3e6c010c3`),
private datasets `news.current_multi_source_evidence` and
`news.current_multi_source_articles`, manual-only collector
`news.current_multi_source`, and host-routed `news.search@2.1.0`. Migration
`news:0008_adopt_fmp_news_legacy` (SHA-256
`ea5302726758ab2bb987a525c3094885e016e4596689d26c8290808523f8327f`)
adopts `fmp_news_articles` as inactive private evidence behind an exact schema
guard, with no collector, tool, dashboard, export, or source ID. Version 2.0
remains FMP-only; v2.1 merges the fixed FMP press/general, Federal Reserve,
ECB, BEA, EIA, and Alpaca/Benzinga feeds. The bounded 2026-08-30 16:00 UTC
proof completed all 20 requests without retry; all eight steps succeeded and
retained 1,069 generic-source articles. Raw evidence and bodies stay private.
On 2026-08-30 the reviewed per-user hourly units were linked, daemon-reloaded,
enabled, and started. That check recorded `loaded`/`enabled`/`active`/`waiting`
for its first normal `:10` UTC trigger; activation did not run the service,
contact a provider, or write a store. Exact
projection removes both migrations, all three datasets, the collector, and
v2.1, restoring byte-identical `2.63.0`/`2.23.0`.


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
- On 2026-08-30 the user explicitly authorized updating that enabled,
  legacy-named timer in place to invoke the fixed 15-ETF grid. On 2026-08-31
  the user authorized moving it from 15:55 to 16:20 America/New_York on
  weekdays. It retains `Persistent=false`, no retry or catch-up, and the
  fixed 362-request, 900,016-row, 256 MiB, 900-second, and 16-minute host
  bounds. The separate 18:00 Stage 12E FMP job is unchanged.
- The authorized 2026-08-31 manual run at 20:01 issued 128 requests, completed
  14 underlyings with XLE partial, retained 110 captures, materialized 12,556
  surface rows, and made 51,465 writes. It was not retried. The later timer
  restart unexpectedly dispatched one additional partial run at 20:29: 64
  requests, six completed underlyings, 54 captures, 8,812 surface rows, and
  27,031 writes with exit status 75. It was not retried or deleted and must not
  be silently repeated.
- On 2026-08-27 the user separately authorized exactly one fixed FMP IWM
  full-history attempt into the additive 96-ETF successor in
  `data/market.sqlite`. That attempt completed with 1,254 rows and passed
  immutable postchecks. The dependent manual fixed 15-ETF, ten-DTE Alpaca
  attempt then issued 17 requests and failed closed for all underlyings before
  publication, with zero writes. Those completed 2026-08-27 attempts may not
  be manually repeated. At that time no timer changed and no completed Stage
  10 or Stage 12C provider unit was reopened.
- Stage 12D is complete as a no-transfer, read-only adoption/freeze proof. It
  authorizes no provider, credential, write, copy, backup, promotion, public
  consumer, or scheduler action. See the
  [contract](STAGE12D_PROJECT_LOCAL_OPERATIONALIZATION.md) and
  [evidence](STAGE12D_EVIDENCE.md).
- On 2026-08-29 the reviewed Stage 12E timer was daemon-reloaded, enabled, and
  started. The activation check recorded `loaded`/`enabled`/`active`/`waiting`,
  a next trigger of Monday 2026-08-31 18:00:00 EDT, and an empty `LastTrigger`.
  At that check the service was `inactive/dead`, with no start or exit timestamp; no
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
  terminal. Any durably received response outside policy becomes a failed
  per-symbol result while later symbols continue; transport/no-response
  ambiguity and publication/store failures remain fail-fast. The timer is
  non-persistent, has no retry or catch-up, targets only
  `data/market.sqlite`, retains per-unit private evidence, and writes
  nothing on exact semantic replay. It does not repeat historical Stage 10 or
  Stage 12C work and does not enable the frozen Stage 7 `market-close` job.
- The explicitly authorized September 2 corrective attempts were each
  single-attempt and were not retried. Runner v1.2 stopped after one
  byte-identical AAPL response because an implementation-version change had
  incorrectly changed its raw-response request scope; it made zero writes and
  its six-file journal was preserved intact. Runner v1.3 fixed that scope and
  issued 616 of 630 requests: 611 published, EA and IRBO were terminal
  noncoverage, AVB and EQR were isolated unapproved-empty results, and a
  malformed 402 error envelope for `^AXJO` stopped ordinal 616 before the
  final 14 symbols. There is no completion receipt. The full immutable market
  check returned `quick_check=ok`, zero foreign-key violations, and no
  WAL/SHM sidecars. Offline-verified v1.4 now isolates every durably received
  response-policy failure while preserving fail-fast transport and
  publication/store behavior; it has not received a second live attempt.
- No other default-path market operation is authorized beyond the exact SPY,
  one-attempt IWM/broad-grid, and Stage 12E daily-refresh exceptions above.

## Canonical company boundary

- The fixed two-request AAPL SEC seed completed on 2026-08-25 in
  `data/company.sqlite`; it must not be manually repeated or broadened without
  a new explicit request identifying the finite scope.
- The original `sec.company.aapl_fundamentals` seed remains private and
  manual-only; its seed decision grants no wider scope. Later SEC roster and
  company-market decisions have separate scopes in
  [Recurring exceptions](#recurring-exceptions) and
  [September 5 fetch repairs](#september-5-fetch-repairs). They do not authorize
  a manual repeat of the seed or an unlisted company operation.

## Canonical news boundary

- The frozen `news:0005_fmp_stock_latest` one-shot contract remains historical
  and is not reopened by either current-feed successor.
- Migration `news:0006_fmp_stock_latest_current` is applied to the canonical
  news store. Its 2026-08-30 proof retained 229 articles from one request with
  no retry. `news.search@2.0.0` remains a local read-only headline-metadata
  reader; raw response bytes and bodies are private.
- Migrations `news:0007_current_multi_source` and
  `news:0008_adopt_fmp_news_legacy` are applied. Version 2.1 reads the fixed
  multi-source captures; 0008 owns the 22,910-row legacy relation as inactive
  private evidence behind a schema guard and provides no source ID or consumer.
- The bounded 2026-08-30 16:00 UTC batch completed all eight source steps and
  all 20 requests without retry, retaining 1,069 generic-source articles.
  Future missing credentials still produce source-local `unavailable`; a
  source with no retained capture may honestly return no records.
- On 2026-08-30 the hourly per-user service/timer units were linked,
  daemon-reloaded, enabled, and started under the user's explicit decision.
  That check recorded active/waiting for the first normal trigger at 17:10 EDT
  (21:10 UTC), an empty `LastTrigger`, and an inactive/dead service
  with no execution timestamps. Activation made no provider request or store
  write. It is non-persistent and has no retry or catch-up.

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

## Repaired H.4.1 and petroleum current refreshes

On 2026-08-31 the user explicitly authorized repairing the recurring
invalid-request outcomes for Federal Reserve H.4.1 and the three EIA
petroleum-fundamentals sources. Two scheduled aggregate runs, on 2026-08-28
and 2026-08-31, had failed the same four steps while continuing the remaining
independent sources.

The bounded repair used four single-attempt, no-write provider diagnostics
followed by four single-attempt canonical publications, with no retry. The
FRED archive returned valid observations outside the requested rolling
window; the parser now ignores those rows while retaining only the declared
inclusive window. Current EIA petroleum responses use MBBL and MBBL/D; the
parser now accepts those exact source-native labels plus the previously
retained descriptive equivalents, without changing canonical units, series
identity, registry bindings, schema, credential handling, or publisher
semantics.

The H.4.1 publication wrote one new observation version for each of its three
series, advancing all three to 1,237 current weekly observations through
2026-08-26. The petroleum publications wrote one new observation version per
series, advancing total-motor-gasoline stocks to 1,912, distillate stocks to
2,291, and finished-motor-gasoline product supplied to 1,855 current weekly
observations, all through 2026-08-21. The descriptor-pinned immutable
post-check returned integrity ok, zero foreign-key violations, and the
expected six current-series totals; no SQLite sidecars remained. The stable
macro-store SHA-256 changed from
2719a8b0db1d8d88fff7ef4c6ed1dffbaa9207da92782838ea60db2145bfe9aa
to
42474cdd2117fc2b6d782decbd8a0119c458fc5af24621fc08d4ed60b35a57e8.

These eight requests and four manual publications are complete and must not
be repeated manually. No service or timer was started, retried, changed, or
reloaded; the existing clock-driven macro-current exception retains its fixed
scope and cadence.

## September 5 fetch repairs

The user's repair-and-live-fetch request added H.8/SLOOS to the existing
macro wrapper and replaced stale CFNAI workbook fetching with its current FRED
feed. H.8/SLOOS use the previous quarter's start through the current quarter's
end; CFNAI starts at 2026-01-01. There are 29 operations and a 94-request cap.
The existing timer cadence is unchanged. This supersedes the September 4
unsuccessful H.8/SLOOS gate for current source eligibility.

The finite January-September macro repair and AAPL actions/annual estimates
pilot are complete and must not be manually repeated. The company collector is
implemented in registry 2.69.0. On September 5, the user explicitly approved its
separate weekday 19:00 America/New_York host scheduler exception. The company
activation check on September 5 recorded it installed, enabled, and
active/waiting, with its first scheduled trigger September 7 at 19:00 EDT.
Activation did not execute the service or change
the inspected stores or company operation state.
The [repair receipt](FETCH_REPAIRS_2026-09-05.md) records scope, limitations,
verification, private evidence, and the completed scheduler activation.

## Recurring exceptions

The recurring exceptions listed in this section have separately recorded
authorization and activation evidence. This inventory specifies their bounded
scope; it is not a fresh check of installed units, next triggers, or service
health. Normal clock-driven execution is the authorized boundary. Without a
new explicit user decision, agents must not manually trigger, change, retry,
broaden, install, start, enable, disable, remove, or repurpose a recurring unit.

### Latest recorded decisions by workflow

Use the linked amendments together with their focused contracts; later explicitly
scoped decisions supersede the earlier baseline below. This is a decision index,
not an inventory of currently installed or healthy services. Finite catch-up
receipts inside a decision do not authorize another catch-up.

| Workflow | Recorded decision / contract |
| --- | --- |
| Daily market prices | [September 23 mapping-guard amendment](#recorded-price-refresh); [selected-price contract](PRICE_HISTORY_COMPLETION_2026-09-11.md) |
| Weekly missing-price repair | [September 15 activation](#recorded-weekly-price-repair), [September 20 empty-response amendment](#recorded-weekly-price-recovery); [weekly repair contract](WEEKLY_PRICE_REPAIR_2026-09-15.md) |
| Macro current, GDP/CPI, employment and calendar | Baseline scopes below; [scheduling contract](SCHEDULING_AND_LOCKING.md) |
| Company dividends, splits and estimates | [September 11 selected-universe activation](#recorded-company-actions); [actions contract](DIVIDENDS_SPLITS_EXPANSION_2026-09-11.md) |
| Selected company inputs | [September 11 expansion](#recorded-selected-company); [collector contract](SELECTED_COMPANY_COLLECTORS_2026-09-11.md) |
| Sharadar primary / conditional SEC facts | [September 19 source policy](#recorded-primary-fundamentals), [September 22 recovery amendment](#recorded-sharadar-recovery); [primary-fundamentals contract](SHARADAR_PRIMARY_FUNDAMENTALS_2026-09-19.md) |
| News | [September 12 batch-size amendment](#recorded-news-batches); [expanded-news contract](NEWS_UNIVERSE_EXPANSION_2026-09-11.md) |
| Derived research publications | [September 21 authorization and activation](#recorded-derived-refresh); [derived refresh plan](../DERIVED_REFRESH_PLAN.md) |
| Equibles earnings-based incremental rotation | [September 25 activation](#recorded-equibles-incremental); [incremental contract](EQUIBLES_INCREMENTAL_REFRESH_2026-09-18.md). The completed historical population remains no-repeat; this decision left its timer unchanged. |
| Theta daily collection and weekend gap repair | [September 26 authorization and activation](#recorded-theta-collection); [Theta collection contract](THETA_DAILY_COLLECTION_2026-09-26.md) |
| Alpaca options collection | [September 26 retirement](#recorded-alpaca-retirement). Retained unit definitions and historical readers do not authorize collection. |
| Options monitor | [September 28 interval-volume amendment](#recorded-options-monitor), following the staggered-radar decision; [monitor contract](OPTIONS_MONITOR_2026-09-26.md) |

### Historical baseline scopes

The table and explanation below preserve the original baseline, with the
previously recorded Alpaca retirement annotation. Do not use superseded request
caps, universes or source-selection rules in place of the amendments above.

| Timer | Fixed scope |
| --- | --- |
| `quant-data-macro-vintages.timer` | 09:05 America/New_York on weekdays. One current BEA GDP/GDI workbook request and one current BLS GDP/CPI request; no retry, migration, credential, or historical-archive fetch. |
| `quant-data-employment-vintages.timer` | First Friday of each month at 10:05 America/New_York. One credential-free BLS request for the fixed payroll and unemployment series; no retry, migration, or Philadelphia Fed historical-workbook fetch. |
| `quant-data-fmp-macro-calendar.timer` | 08:15 and 08:45 America/New_York on weekdays. One bounded current-window FMP calendar request with no retry. The response is retained as wholesale raw evidence before independent GDP/CPI and employment normalization. |
| `quant-data-macro-current-refresh.timer` | 18:30 America/New_York on weekdays. Twenty-nine established macro collector operations run sequentially with a total provider-request cap of 94, no retry, a fixed `data/macro.sqlite` target, and semantic no-write behavior when content is unchanged. It covers Treasury curve; both CFTC futures-only positioning families; Treasury securities auctions; NY Fed overnight rates including SOFR distribution, volume, index, and compounded averages, plus Primary Dealer Statistics; Federal Reserve IORB, target bounds, H.4.1, H.8, SLOOS, and INDPRO; repo facilities and SOMA; Chicago Fed NFCI/ANFCI components and CFNAI; BIS credit conditions; CMDI; Treasury cash, Debt to the Penny, and Monthly Treasury Statement receipts, outlays, and deficit/surplus; EIA gas storage, weekly crude-oil stocks, weekly gasoline and distillate stocks, finished-gasoline product supplied, and monthly electricity retail; NBER recession chronology; BLS PPI, earnings, productivity, and ECI; and BEA personal income, disposable personal income, and personal consumption expenditures. |
| `quant-data-alpaca-spy-options.timer` | **Retired by explicit user decision September 26, 2026; no recurring activation remains.** Historical scope: legacy unit name retained for the authorized in-place 15-ETF grid update. It runs at 16:20 America/New_York on weekdays with `Persistent=false`, an OPRA gate, and no retry or catch-up. It targets only `data/market.sqlite`, the fixed paper/indicative feed, the 15 named ETF identities, and ten fixed DTE targets. One invocation is capped at 362 requests, 900,016 rows, 256 MiB, and 900 seconds with a 16-minute host timeout; raw response bytes precede normalization, partial grids exit nonzero, and exact semantic replay writes nothing. |
| `quant-data-sec-company-fundamentals.timer` | 07:15 America/New_York on weekdays. It reads the fixed Stage 10 equity roster, performs one bounded SEC ticker discovery, processes sequential submissions/CompanyFacts pairs without retry, and targets only `data/company.sqlite`. |
| `quant-data-company-market-refresh.timer` | 19:00 America/New_York on weekdays. The zero-argument wrapper reads at most 700 retained FMP equities and existing company identities, makes one SEC ticker discovery, and fetches dividends, splits, and one annual-estimates page for each unambiguous existing identity. It publishes only to `data/company.sqlite`, creates no identities, and is capped at 2,101 requests, 128 MiB, and 30 minutes with a 31-minute host timeout. Partial coverage exits nonzero, exact semantic replay writes nothing, and there is no retry or catch-up. |
| `quant-data-market-close.timer` | Activation was recorded on 2026-08-29, with no service execution, state directory, provider request, or canonical write during activation. The dated observations are in the canonical market boundary above. On weekdays at 18:00 America/New_York, it snapshots every current FMP provider-native Stage 10 `equity`/`etf`/`index` identity. The 2026-08-29 preflight contained 630 (519 equity, 96 ETF, and 15 index); the dynamic batch is bounded to 800 and begins with `AAPL`. It makes one current-session daily-OHLCV request per symbol. An empty `AAPL` stops the batch. Pinned historical noncoverage symbols remain eligible; only their reviewed empty/HTTP 402 outcomes are terminal. Any durably received per-symbol response outside the accepted status, redirect, media, envelope, or payload policy is retained as a failed result while later independent symbols continue; any such result prevents completion and exits nonzero. Transport/no-response ambiguity and publication/store failures remain fail-fast. It is non-persistent, has no retry or catch-up, targets only `data/market.sqlite`, retains per-unit private evidence, and writes nothing on exact semantic replay. |
| `quant-data-current-news-refresh.timer` | Hourly at `:10` UTC. Following the September 6, 2026 decision, it runs ten fixed source steps: FMP stock/press/general, Federal Reserve, ECB, BEA, EIA, Alpaca/Benzinga, Finviz, and FinancialJuice. The two websites add one credential-free request each; the generic request ceiling is 24 plus one FMP stock-latest request. Coverage derives from the bounded current market universe, requests have no retry, missing credentials are source-local unavailable outcomes, exact replay writes nothing, and the timer is non-persistent with no catch-up. |

These exceptions do not enable any recovered Stage 7 job, provider or series
beyond the exact scope above, a different cadence, catch-up run, or historical
backfill. The aggregate current refresh and the Stage 12E exception leave
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
or scheduler action beyond the recurring exceptions listed above.

## Completed Inspector calendar repair — 2026-09-05 Eastern

The user explicitly authorized fixing the reported genuine failures and one
bounded live repair. At 2026-09-06T02:38:44Z, one current-window FMP request
(2026-08-18 through 2026-11-15, no retry) completed through the existing macro
publishers: 68 compact raw event versions, four GDP/CPI versions and four
employment versions. Legacy calendar counts stayed unchanged and targeted
foreign-key checks passed. SOMA/petroleum were identified as weekly sources;
the old NIPA and wholesale calendar datasets are displayed as historical or
legacy. No manual aggregate run, history repeat, migration or unit mutation
occurred. The finite manual scope is complete, with no repeat authorization.
The [repair record](INSPECTOR_REFRESH_REPAIR_2026-09-05.md) contains evidence,
UI semantics and private successful/no-op fetch tracking details.

## Inspector durable fetch outcomes — 2026-09-06

The user requested durable live-fetch status and its connection to the existing
local Status page. All nine existing recurring CLI forms now save private
per-run receipts; their installed module arguments, working directories and
existing data-directory write permissions were checked read-only. No unit,
cadence, request cap or canonical publisher changed, and no collector was
manually triggered. The Inspector alone was restarted on loopback port 8766;
Status and process-health reads returned HTTP 200.

At observation time `2026-09-06T16:10:59.283893Z`, the normal Current news
12:10 EDT slot had saved start `2026-09-06T16:10:17.415648Z`, completion
`2026-09-06T16:10:33.344334Z` and exit code 0. The production Status reader
reported this as Completed with saved-run evidence. This is an observed batch
outcome, not an assertion about individual-source freshness or new canonical
rows. Earlier runs without retained evidence remain Unconfirmed. This change
grants no new provider or scheduling authority; mechanics and reader limits
are in the [Status contract](TOOL_PLATFORM_SPEC.md#live-fetch-status-calendar).

## Closed operations

Without explicit scope authority and the applicable local compatibility,
bounded operational, or heavier gate (an accepted contract alone is not
execution permission):

- do not make a live provider request outside the listed clock-driven
  exceptions or a current explicitly authorized finite workload;
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


## September 6, 2026 — FMP research repair and local live quote

The owner authorized MSFT/AAPL FMP input repair, MSFT/AAPL/SPY price-gap
repair, a local `price_realtime(ticker)` tool and an agent handoff. Historical
estimates/consensus are deferred. The finite collection is capped at 40 FMP
requests with no retry. The owner also authorized restarting Ubuntu WSL
after its connection failure; normal recurring-unit scope and schedules
remain unchanged. See the [focused contract](FMP_RESEARCH_INPUTS_CONTRACT_2026-09-06.md)
and companion handoff for exact scope and actual activation evidence.

### FMP repair completion evidence

Canonical publication completed at `2026-09-06T18:32:28.751777Z`:
company migration 0008, 26 research snapshots / 385 source rows, and 34 missing
MSFT/AAPL/SPY daily-price versions. Full offline coverage was 1,607 unique
successful cases across retained/resumed runs, with independent review.
Post-publication public readers and immutable checks passed. All 29 bounded
retained-response replay checks returned unchanged and zero canonical writes;
company and market main-file hashes, sizes and mtimes were identical.

The finite FMP ledger closed at **35 attempts / cap 40**, with no retry:
25 new company responses, four HTTP 402 availability checks, three price
responses and three quote checks. One existing AAPL annual estimate response
was reused without a request. This completed population is not authorization
to repeat collection or backfill; a new repeat requires its own finite scope.
`price_realtime` performs one request per explicit local invocation.

Ubuntu was restarted twice under the owner's authorization after host control
connection failures. No recurring unit was manually triggered or reconfigured
by this repair. An authorized normal hourly news run completed during testing;
the affected unchanged data-guard test was rerun successfully afterward.
See the [completed handoff](FMP_RESEARCH_INPUT_HANDOFF_2026-09-06.md)
for receipts, tool usage, source conflicts and remaining gaps.


## September 6, 2026 — shared Finviz and FinancialJuice activation

The user authorized both website sources, their integration into the existing
hourly current-news fetcher, and a forward migration of the shared news tables.
This decision extends the prior eight-source batch by these two sources only.
See the [source contract](WEBSITE_NEWS_SOURCES_2026-09-06.md) for fixed URLs,
identity, time precision, transport bounds, replay behavior, and reader scope.

The reviewed 59-file integration was installed at registry `2.72.0` after
1,625 unique offline cases passed across retained/resumed runs and a fresh
independent review reconciled all evidence. Ten focused registry/fetcher checks
also passed in the installed main checkout.

At `2026-09-06T18:53:43.905856Z`, the existing news-store migration runner
applied `news:0009_website_source_extension` at SQL SHA-256
`992f83f7c9fdd49eade78ab52fc01221746e78f4abe1e851641c75be184f860b`.
Every pre-existing table row and trigger definition survived the migration
unchanged, including 7,993 shared articles, 11,513 FMP stock-current articles,
and 22,910 legacy FMP articles. The first eight migration ledger rows remained
identical. Integrity was `ok` with zero foreign-key violations.

The one-time retained-response publication completed at
`2026-09-06T18:54:30.098059Z`, adding **180 Finviz and 100 FinancialJuice**
articles to the existing multi-source tables. It made **zero new provider
requests**, preserved every prior row and old-source article count, and passed
both repository and public `news.search@2.3.0` checks. The source-native
responses retain their original capture times of 04:55:19 UTC and 04:56:03 UTC;
this was not a fresh provider poll. The initial publications are complete and
must not be manually repeated without a new finite request.

The observed timer remained enabled/active/waiting, with its next normal slot
at 19:10 UTC (15:10 EDT). The service remained inactive/dead with exit status 0
from its preceding normal run. No recurring unit was manually triggered,
restarted, reloaded, enabled, disabled, or edited. The installed zero-argument
module now includes both websites in the next normal ten-source batch; that
future website execution was not observed during activation.

The previously authorized manual loopback Inspector was restored on port 8766
at 18:55:15 UTC with PID 19154 and registry `2.72.0`, after confirming the
port was unoccupied. Both website filters returned HTTP 200 and ten retained
headlines each without a page error; the Status page returned HTTP 200.

Private execution evidence is retained in
`.local/news-install-receipt-20260906.json`,
`.local/news-website-validation-20260906.json`,
`.local/news-website-activation-20260906.json`, and
`.local/news-inspector-reload-20260906.json`. Full offline logs and the
independent closeout remain under `.local/news-html-worktree/.local/`.

## Equibles transcript backfill decision — 2026-09-07

The user explicitly authorized raw transcript storage for all existing 500+
tickers and daily pacing within the Equibles 100-request quota, completing each
ticker's full available history before advancing. The
[finite backfill contract](EQUIBLES_TRANSCRIPT_BACKFILL_2026-09-07.md) binds this
to the frozen 519-equity universe, company-only migration 0010, named existing
credential, two fixed endpoint shapes, retained response reuse, and dedicated
00:10 UTC host timer. This decision supersedes the prior evaluation-only
restriction for this population and includes initial execution and activation.
Other Equibles datasets, existing recurring units, and completed FMP populations
are outside this authority. Dated activation evidence follows.

### September 7, 2026 — Equibles activation evidence

The combined 2.74.0 integration passed independently reconciled offline coverage
for all 1,668 unique current cases and fresh independent review. Exact earlier
failures, corrections, and retained-run provenance remain in the
[focused validation receipt](EQUIBLES_TRANSCRIPT_BACKFILL_2026-09-07.md#offline-validation-receipt--2026-09-07).

The separately authorized FMP analyst task first applied company 0009 at
`2026-09-07T06:50:51.596529Z` under pinned registry 2.73, published its
26 retained AAPL/MSFT pilot captures, and passed 20 no-change replays,
16 reader/cutoff checks, integrity, and foreign-key checks. Its private
`pilot-activation.json` and `pilot-verification.json` receipts are under
`.local/fmp-analyst-history-20260907/`; see the
[FMP analyst contract](FMP_ANALYST_HISTORY_2026-09-07.md).
After its quiet handoff, company 0010 was applied at
`2026-09-07T07:01:09.081284+00:00` under registry 2.74.0, SQL SHA-256
`600358ba2c8c3196ef4c421f42444fe2955222fbdcb26a75c41e7aa1e6a74e62`.
All prior company migration rows remained byte-for-byte equivalent field values.

The frozen population contains 519 existing FMP equities. Initialization reused
15 Equibles responses and reserved the earlier 40 September 7 evaluation calls
against the same daily quota. The initial service ran at 07:03:51–07:06:19 UTC,
made 60 new requests, and ended successfully at 100 total requests / zero
remaining. It stored 60 complete transcripts and 60 raw pages, with 4,462
speaker turns and 3,511,798 original bytes: A 26 calls, AAPL 27, and ABBV 7.
A and AAPL are complete for the available catalogue; ABBV is the saved current
ticker. The immutable audit verified every raw hash and retained byte sequence,
complete bundles, artifact/snapshot lineage, zero foreign-key violations,
unchanged predecessor ledger rows, and unchanged pre-existing company-table
counts, including the analyst pilot. The company window was handed back to the
FMP task for its separately authorized retained-only bulk publication.

Only the new Equibles service and timer were linked. The user timer was enabled
at `2026-09-07T07:10:40.131847+00:00`, observed loaded/enabled/active/waiting,
and scheduled next for **2026-09-08 00:10 UTC (September 7, 20:10 EDT)**.
It runs daily at 00:10 UTC with persistent catch-up and durable shared-account
quota accounting; the service has no restart. The service was inactive after
the successful first batch, and timer enablement added no requests.
Existing recurring units were not restarted or reconfigured.

This is an active finite backfill, not a completed 519-ticker population.
Normal clock execution may continue its saved ticker-first workload within the
existing bounds. Completed calls are not refetched; new symbols, a completed
population repeat, or periodic refresh are outside this decision. WSL must be
running for the host timer to execute.

Private receipts: `.local/equibles-activation.json`,
`.local/equibles-first-batch-audit.json`, `.local/equibles-first-service.log`,
and `.local/equibles-timer-activation.json`. The
[full activation receipt](EQUIBLES_TRANSCRIPT_BACKFILL_2026-09-07.md#activation-receipt--2026-09-07)
records exact storage, checkpoint, schedule, and limitations.


## FMP analyst population completion — 2026-09-07

The current user decision explicitly authorized AAPL/MSFT recommendations and
price targets, the longest returned related history, then the retained single-name
universe. It superseded the earlier analyst deferral for this finite population.
The user separately approved coordination with the transcript task. The ordered
company 0009 pilot and company 0010 activation evidence above remains unchanged.

After the transcript post-write handoff, retained-only FMP publication completed
without another provider or migration call. The canonical company store now has
516 of 519 retained equity symbols (513 issuers), 397,285 distinct observations,
397,299 versions, 4,971 captures, and 397,525 capture memberships. Annual/quarter
estimates, earnings, and historical recommendation distributions cover 516 symbols;
recommendation events/consensus cover 515; price-target events/consensus/summary
cover 512. AVB, EA, and EQR remain unconfirmed identity mappings. ERIE has empty
recommendation responses; BF-B, ERIE, L, and NWS have empty target responses.

The universe capture used 4,962 single attempts (4,961 FMP plus one SEC discovery),
all HTTP 200, 116,585,138 returned bytes, and 3,707.60 seconds. It stayed within
15,000 attempts, 1 GiB, two hours of capture execution, the endpoint/page bounds,
and the no-retry policy. With the separately scoped 22 pilot FMP requests, the
task used 4,984 external requests total. The 4,959 eligible retained responses
produced 4,945 successful publications and 14 empty no-change outcomes; two empty
pagination-stop responses added no data. No completed FMP population was refetched.

The independently reviewed combined 1,668-case offline gate preceded activation.
The final immutable audit passed integrity and foreign keys. Thirteen universe
replays and twenty pilot replays caused zero canonical change, with complete
company-table counts and database SHA-256/size/mtime preserved; sixteen pilot
reader/cutoff checks passed. Bulk publication preserved every non-analyst company
count, including Equibles data, and the entire company 0001–0010 migration ledger.
Source conflicts and missingness remain explicit. Historical forecast periods
are not historical estimate vintages; availability is the retained local capture.

This one-time FMP population is complete with those gaps. Its manual collector is
not scheduled. Do not repeat this population, add unresolved issuer mappings, or
install a recurring analyst refresh without a new finite scope or explicit schedule.
See the [focused population receipt](FMP_ANALYST_HISTORY_2026-09-07.md#universe-population-evidence)
and private `.local/fmp-analyst-history-20260907/completion-receipt.json`.


## Equibles Inspector and Status integration — 2026-09-07

The user requested Equibles in the Data Inspector and Status page, and a single
expandable current-news entry in Daily focus. The completed local integration
adds a bounded retained-transcript view, the existing Equibles timer to the fixed
read-only schedule mapping, a sanitized checkpoint progress projection, and
observational run history for the unchanged zero-argument Equibles collector.
No recurring unit was triggered, reconfigured, enabled or disabled. No provider
request, quota change, canonical write, migration or registry change occurred.

The local manual Inspector was reloaded on loopback port 8766. Browser checks
verified the transcript catalog and readable original speaker turns, current
backfill progress, collapsed hourly news outcomes, keyboard/no-script operation,
and 320/360-pixel layouts. The final Data status HTML also showed Equibles'
recorded 2/519 completed companies, 60 stored calls and daily 00:10 UTC schedule.
The existing immutable reader verified 27 retained AAPL transcript captures;
all inspected canonical store size/mtime stamps were unchanged.

Validation passed 75 unique focused cases across the recorded run and corrected
rechecks, plus 19 isolated-fixture and 19 live browser assertions. The original
failed assertions and their corrections remain in private evidence. Full offline
suite execution was not required for this bounded local read-only UI change.
Astra UI review covered desktop/mobile progress, expanded news and actual long
transcript paragraphs. No material visual findings remain. Private receipts,
logs and screenshots are under `.local/equibles-ui-20260907/`; the fixture server
verifies unchanged store fingerprints during teardown. Ongoing run-history
records begin with normal subsequent Equibles executions; earlier missing
execution evidence is not invented.

## September 8, 2026 — transcript analysis implementation

The user requested implementation of forward-guidance extraction, analyst
concerns and management wording assessments, selecting Terra for extraction and
Sol for pilot review. The resulting manual pipeline, fixed store binding,
company migration 0011 and registry 2.75.0 are described in the
[transcript analysis contract](TRANSCRIPT_ANALYSIS_CONTRACT_2026-09-08.md).
Implementation alone is not a paid pilot workload or recurring-unit decision.
No operational migration, model request or analysis schedule was executed during
this implementation. Before paid execution, a finite stored-capture plan and
request/spend cap must be approved. Existing Equibles clock execution retains its
prior authority.

## September 8, 2026 — Equibles missing-quarter correction and bounded continuation

The user explicitly authorized skipping missing quarters and resuming the unused
daily allowance. The [updated Equibles contract](EQUIBLES_TRANSCRIPT_BACKFILL_2026-09-07.md#missing-quarter-continuation--september-8-2026-september-9-utc)
supersedes the original transcript-404 pause with retained gap evidence and
next-quarter continuation, preserving ticker-first order and complete-call-only
publication. Authentication and provider failures remain errors.

The observed pending ADM 2023 Q3 attempt `2026-09-09-087` returned HTTP 502,
not a missing-transcript response. A single controlled retry plus continuation is
bounded to **13 new requests in UTC day 2026-09-09**, using the existing named
credential, frozen 519-ticker workload, publisher and locks. Prior attempts and
failure evidence remain charged and retained. This decision does not reconfigure
or restart a recurring unit, apply a migration or authorize additional retries.
Execution finished at `2026-09-09T01:23:03.708883Z`: exactly 13 new GETs,
all HTTP 200, added 12 complete ADM transcripts and discovered ADP's 28-call
catalogue. The ledger is now 100 attempts / 0 remaining. Canonical coverage is
251 calls across 10 completed tickers, with ADP next and no pending error.
No quarter was skipped on this run; ADM 2023 Q3 succeeded on its controlled retry.

The completion audit verified new raw hashes, retained bytes, full-call bundles,
lineage and transcript foreign keys, preserved the prior 239 transcript records
and page manifests, and confirmed the migration ledger was unchanged.
Twenty-five focused collector/storage/status tests passed. Private authorization,
result, audit and validation receipts are retained under
`.local/equibles-missing-quarter-fix-20260909/`.

## September 9, 2026 UTC — Codex subscription transcript pilot

The user authorized implementing Codex subscription execution and triggering a
run for the ticker whose transcript downloads had finished. The disclosed finite
scope is **ADM's 26 retained transcripts**, split into 10/10/6 immutable plans,
one Terra/high extraction plus one Sol/high review each, **at most 52 model runs**.
This uses the existing WSL ChatGPT login with no API-key fallback or API spend;
shared subscription allowance still applies. Failed or uncertain requests are
retained and are not automatically retried.

The [subscription adapter contract](TRANSCRIPT_ANALYSIS_CONTRACT_2026-09-08.md#codex-subscription-adapter-and-adm-pilot--2026-09-09-utc)
also covers the necessary application of the already-reviewed company 0011,
whose bytes remain unchanged. No new migration, registry version, Equibles
request or recurring-unit change is authorized by this pilot. The exact
26-capture manifest and pre-activation ledger/count evidence are retained in
`.local/transcript-codex-pilot-20260909/pilot-preflight.json`.
Company 0011 was applied at `2026-09-09T02:05:45.715315Z` with the reviewed
checksum and all prior migration/data evidence preserved. Six analysis tables
are now active.

The ADM pilot stopped at `2026-09-09T02:10:52.707391Z`: the first Terra/high
attempt (fiscal 2026 Q2) reached its 180-second deadline without a returned
message or usage record. **One attempt started; zero analyses and zero reviews
were published; 51 planned runs were not started.** The failed/uncertain receipt
blocks silent retry, including through a new plan. No API fallback, Equibles
request or recurring-unit change occurred. Subscription usage is unknown.
A cache compatibility diagnostic is retained, without claiming it caused the
timeout. Longer execution or a controlled retry needs a new finite decision.

The immutable audit verified all 251 raw page hashes/bytes, all 26 selected
source hashes, unchanged post-activation migration ledger, empty analysis
tables and zero analysis foreign-key errors. No worker remained. Fifty-six
focused tests, two native localhost CLI fixtures and independent review passed;
the first live attempt did not establish extraction quality or successful
subscription publication. See the [dated contract result](TRANSCRIPT_ANALYSIS_CONTRACT_2026-09-08.md#activation-and-first-live-attempt)
and private `completion-audit.json` in the evidence directory above.


### Authorized ADM retry — 2026-09-09 UTC

The user explicitly approved one retry of the failed ADM fiscal 2026 Q2
extraction with a **1,200-second (20-minute) deadline**, retaining Terra and
**high** reasoning. This adds exactly one model attempt to the prior pilot
allowance (53 total including the original failed attempt and the 51 still
unattempted planned calls), with no further automatic retries or API fallback.
The longer bound is per retry transport; the ordinary default remains 180
seconds and fixed prompts, schema, request identity and model settings do not
change. Original failed attempt receipts and native events remain untouched.

The controlled one-time runner holds the existing analysis job lock, records
a durable one-attempt admission in
`.local/transcript-codex-retry-20260909/attempt.json`, and uses a separate
native evidence directory for attempt two. A successful returned response is
retained before the established validation/publisher, using actual retry
timestamps. Publication reuses the original semantic request identity so the
original plans can reuse it without another extraction. There is no deletion
of failed evidence or reset of the original plan's charged request.


### Result of the authorized 20-minute retry

The single retry started at `2026-09-09T02:23:30.803541Z`. Native Codex
completed successfully in **196.87 seconds** with Terra/high and a 1,200-second
deadline, returning a structured draft. Reported usage was 20,659 input tokens
and 10,748 output tokens (31,407 total; 2,033 reasoning-output tokens reported).
This establishes a returned result through the subscription adapter, without
an API-key fallback. It does not establish successful canonical publication.

The draft contains 20 guidance claims, 14 analyst question blocks, seven topics
and six management-tone assessments. The existing validator rejected the first
missing numeric scale. Diagnostic checks found additional period/shape
inconsistencies, five source-unit scale mismatches (million/billion values
paired with scale 1), and an answer-evidence reference outside the topic's
question membership. These are retained draft errors, not accepted facts.
No numeric correction, source relabeling or validator relaxation was applied.

**Zero analyses and zero reviews were published.** Sol review and the remaining
51 original model runs were not started because this extraction failed
validation. The original failed attempt and all native evidence remain intact;
the pilot has two actual model attempts in total. All 251 raw transcript pages
passed hash/preservation checks and the post-activation migration ledger is
unchanged. The exact returned response, diagnostic pointers and completion
audit are in `.local/transcript-codex-retry-20260909/`.

Sixteen focused Codex tests passed, including the bounded per-transport
deadline override and preservation of the default deadline, high reasoning,
failure cleanup and no automatic retry. The source/schema/model configuration
and canonical validators are unchanged. The next extraction revision needs
explicit instructions for numeric scale, shape/period compatibility and topic
citation membership; this one-retry authorization does not admit another
model attempt.

## Company-universe expansion planning decision — September 8, 2026 (September 9 UTC)

The user requested a revised implementation plan targeting the 2,248-ticker
Major Index Liquid CSV with Sharadar fundamentals, FMP statements, FMP annual
and quarterly analyst estimates, and Equibles raw transcripts. The user reports
a paid Equibles allowance of 100,000 calls/day. This replaces the proposed
targeted FMP/519-ticker transcript scope for the planned expansion.

The [implementation plan](COMPANY_UNIVERSE_EXPANSION_PLAN_2026-09-08.md)
owns migration sequencing, explicit collector-universe bindings, retained-data
reuse, quota/checkpoint conversion and future activation gates. Existing
operational receipts retain their original scope and dates. This entry is
planning evidence only: it enumerates no new finite live workload and records
no provider request, credential read, store access, code/registry/migration
change, timer/service action or quota-ledger mutation. Existing normal clock
execution remains under its recorded authority.

### Full collection-matrix planning amendment — September 8, 2026 (September 9 UTC)

The user subsequently accepted the remaining collection recommendations and
requested that all be included in the implementation plan: expand daily equity
prices while retaining the ETF/index selections; SEC filings/CompanyFacts for
supported issuers with per-CIK request deduplication; dividends, splits and
earnings dates for supported members with workload sizing; larger-universe news
matching with source-specific request plans; and separate coverage review of
other distinct FMP inputs. Options and macro retain their independent scopes.

The newer full-universe FMP statements/annual-quarterly estimates/transcripts
decisions and reported 100,000-call/day Equibles allowance continue to apply;
the older table's targeted FMP/initial-519 transcript suggestions are not
reinstated. The [revised plan](COMPANY_UNIVERSE_EXPANSION_PLAN_2026-09-08.md)
now includes every collection row, its owning store, worker dependencies,
provider budget coordination, rollout and validation. This remains a plan
update: no finite live acquisition, code/schema/registry change, canonical-store
access or recurring-unit action was executed by this amendment.


### September 9, 2026 UTC — transcript extraction investigation and fix

The user requested investigation and correction of the rejected ADM extraction.
The private request revision `transcript_analysis_prompt.v2` makes existing
numeric-shape and fiscal-label constraints visible during generation, explains
numeric multipliers and topic evidence membership, and retains Terra/high plus
Sol/high. New Codex v2 configurations use the previously requested 20-minute
deadline, subject to the existing finite request and invocation caps.

Existing canonical validators, output schema, registry and migration bytes are
unchanged. Legacy plans execute their original prompt/configuration and retain
their failed-attempt protection. Validation reports now include precise field
pointers. The failed ADM raw response remains unchanged and unpublished.

This implementation used local fixtures and read-only retained evidence; no new
live model attempt, Equibles request, operational migration or recurring-unit
change was performed. Applicable execution scope must be resolved before a new
v2 extraction; the prior one-retry receipt is not cleared or reused as fresh
authorization. See the [request v2 correction](TRANSCRIPT_ANALYSIS_CONTRACT_2026-09-08.md#extraction-request-v2-correction--2026-09-09-utc).


### September 9, 2026 UTC — approved ADM v2 live check

The user approved the prepared plan
`d564c36b566e02e5374d9d0daec9fcd9b56515cbb9dbb01b7a02d0ddff9376bc`:
ADM fiscal 2026 Q2, one Terra/high v2 extraction plus one Sol/high review,
**at most two new model requests**, each limited to **1,200 seconds**. This
new decision authorizes the named v2 check; the old failed attempts stay
retained. Existing WSL ChatGPT authentication, fixed company store, immutable
source reader, job lock and replay-safe publisher are reused. There is no
API-key fallback, automatic retry, Equibles fetch or recurring-unit change.
Execution receipts are retained in `.local/transcript-analysis-v2-live-20260909/`.


### Result of the approved ADM v2 live check — 2026-09-09 UTC

Both approved calls returned through the existing ChatGPT subscription:
Terra/high in **226.463 seconds**, then Sol/high in **332.911 seconds**.
Exactly **two new model calls** consumed this check's allowance; neither timed
out and no automatic retry or API fallback occurred. Their reported token totals
were 34,526 and 38,430 respectively (72,956 combined, including reasoning).

The Terra draft passed the guidance value/period field checks for all 19 claims,
but four of its 88 citations did not occur verbatim in their cited source turns.
The established validator rejected publication. The second approved call used
the exact retained, rejected draft for a private Sol review. No canonical parent
analysis was fabricated. Sol declared `needs_changes`, with 16 draft findings,
but two of its own evidence quotations also failed exact-source validation.
Neither model output is accepted canonical research.

The immutable audit at `2026-09-09T03:08:43.680884Z` confirmed **zero analyses
and zero reviews**, all six analysis tables empty, all 251 pre-existing raw
transcript pages preserved byte-for-byte, unchanged migration history and zero
analysis-table foreign-key errors. Both exact responses and native execution
events are retained. Canonical replay is inapplicable because nothing was
published. The original pipeline report correctly records its single extraction
call; the separate private review receipt and aggregate `final-audit.json`
account for the second call. No additional model workload was started.

The extraction/review citation failures remain unresolved. Source-selected
citation spans are a possible follow-up design, not an implemented correction.
See the [detailed v2 result](TRANSCRIPT_ANALYSIS_CONTRACT_2026-09-08.md#result-of-the-approved-v2-live-check--2026-09-09-utc)
and private evidence in `.local/transcript-analysis-v2-live-20260909/`.


### September 9, 2026 UTC — evidence-summary implementation decision

The user explicitly requested relaxed quotation checks with source turn IDs,
retained numeric/reference validation, and a concise cross-sector guidance
checklist. New prompt v3 / output v2 preparations use evidence_summary, with
code-attached exact source-turn provenance and null summary quotation offsets.
Legacy request bytes/configurations/output semantics and failed receipts remain
unchanged. The [current transcript contract](TRANSCRIPT_ANALYSIS_CONTRACT_2026-09-08.md#evidence-summaries-and-cross-sector-checklist--2026-09-09-utc)
owns the versioned behavior and checklist. This is local implementation authority;
no new model workload, operational migration, Equibles request or recurring-unit
change is authorized or executed by this decision.

### September 9, 2026 UTC — structured coverage and finite ADM rerun

The user requested the structured coverage ledger, reconciliation across
prepared remarks and Q&A, cross-section claim links and precise Sol findings,
then explicitly requested another run. This authorizes one new prompt v4
extraction and pilot review of ADM fiscal 2026 Q2 only, capture
equibles_transcript_94c0ceecdd9e040ee16ba5fb8f73df91: at most two new requests,
Terra/high then Sol/high, through the existing Codex subscription, with a
1,200-second deadline per request and no automatic retry or API fallback.
Use the existing fixed company store, immutable reader, append-only publisher
and locks. No schema migration, Equibles call or recurring-unit change is part
of this scope. Preparation and actual outcomes belong to
.local/transcript-coverage-v4-20260909/; authorization is not execution evidence.
The [transcript contract](TRANSCRIPT_ANALYSIS_CONTRACT_2026-09-08.md#structured-coverage-and-reconciliation)
owns the new derived output behavior.

#### Execution result for the September 9 structured-coverage rerun

Both authorized calls returned within their deadlines: Terra 278.870 seconds,
Sol 431.374 seconds. Terra's 24-claim draft failed a candidate/claim source-link
check before publication. The second approved call reviewed the unchanged
rejected draft privately; Sol returned needs_changes (18 errors, one warning).
Exactly two model requests were admitted, with no retry or API fallback.
No analysis/review rows were published; all 251 pre-existing raw transcript
pages and the migration ledger were preserved. The full receipts, private
outputs and audit are under .local/transcript-coverage-v4-20260909/.
This exhausts the finite workload recorded above and grants no additional run.

### September 9, 2026 UTC — one-call Sol extractor comparison

After the source adjudication of Sol's ADM review, the user accepted implementing
a Sol/high extraction comparison. The finite workload is exactly one new
gpt-5.6-sol extraction of ADM fiscal 2026 Q2, capture
equibles_transcript_94c0ceecdd9e040ee16ba5fb8f73df91, using the unchanged prompt v4
and analysis schema v3 through the existing Codex subscription login.
The call retains high reasoning and a 1,200-second deadline, with no model retry,
API fallback, separate model review, Equibles call or recurring-unit change.

The comparison is private: migration 0011 and its Terra-only analysis constraint
remain unchanged. Reuse the fixed company store's immutable reader, existing
job-lock primitive and Codex adapter; no canonical publisher is called.
Implementation, preparation and actual results belong under
.local/transcript-sol-extractor-20260909/ with operational pilot receipts under
data/.operations/transcript-analysis/extractor-pilots/. Cross-sector or fleet-wide
execution is not part of this one-capture workload.

#### Result of the one-call Sol extractor comparison

The authorized Sol/high extraction returned in 640.035 seconds. Its exact
response is retained privately: 31 guidance claims, 28 candidates, 23,309 input
tokens and 19,884 output tokens. The output-token bound failed; a separate
offline diagnostic also found two candidate/topic reference failures.
The manual comparison found six of eight targeted prior issues corrected, one
partly corrected and one unresolved; it grants no extra execution authority.

Exactly one new extraction ran, with no retry, model review or API fallback.
No canonical analysis rows or migrations changed; all 251 pre-existing raw
transcript pages were preserved. Private replay used zero model calls.
Full receipts and comparison are under .local/transcript-sol-extractor-20260909/.
Production defaults, schema and the output allowance remain unchanged.


## Approved selected-universe bootstrap — September 9, 2026 UTC

After completion of the offline implementation, the user explicitly approved
the prepared FMP/Nasdaq/Equibles bootstrap: at most nine requests, 33 MiB and
600 seconds, with no retries. This scoped execution decision supersedes the
earlier planning-only restriction solely for that exact acquire-only manifest
(SHA-256 39354334d291628de0f4030cdbc0df12ea8e49ffaf3add155b3f4f0a8a41893f).
Existing named FMP_API_KEY, SHARADAR_API_KEY and EQUIBLES_API_KEY resolvers were
used. No canonical-store, scheduler or paid checkpoint activation was included.

At 16:01 UTC the bootstrap reserved eight attempts and retained seven HTTP 200
responses (28,642 bytes; 7.960 seconds). GOOG, GOOGL and MSFT FMP profiles and
Nasdaq TICKERS/SF1/INDICATORS metadata succeeded. The representative Nasdaq
TICKERS data request had an unknown/unusable transport outcome; its attempt
remains reserved with no retry. The source-local stop left the INDICATORS
data page unattempted. A separate MSFT Equibles catalogue request succeeded.

Equibles reported limit 10,000, remaining 9,900 and reset at September 10
00:00 UTC. The prior user-reported 100,000-call capacity is therefore not
verified for this credential/endpoint. Preserve this extra manual attempt in
future account reconciliation; this run changed no existing quota ledger,
paid configuration or recurring workload. The 27-entry MSFT catalogue body
matches prior retained bytes; its new capture and quota evidence remain
separate. The explicit fresh-observation approval covered that overlap.

The responses and single-use attempt journal remain private under
data/.operations/collection-preflight/39354334d291628de0f4030cdbc0df12ea8e49ffaf3add155b3f4f0a8a41893f/.
No canonical database was opened or written. No migration/import, schedule,
recurring-unit action, commit or push occurred. Do not repeat this bootstrap
or retry its unknown request without a new explicitly finite authorization.
The [implementation plan](COMPANY_UNIVERSE_EXPANSION_PLAN_2026-09-08.md#approved-bootstrap-execution--2026-09-09-utc)
records the outcome and the locally tested lowercase Nasdaq metadata-type fix.


## September 9, 2026 — selected-universe backfill and live-fetcher rollout

The user explicitly approved storage activation, provider mappings/budgets,
missing-history backfill, and expanded live fetchers for the 2,248-member
Major Index Liquid selection. This supersedes the earlier planning-only and
nine-request bootstrap-only boundaries for this rollout. Options, macro, and
transcript-model analysis keep their independent scopes.

The reviewed storage migrations and selected membership are applied. Sharadar
and SEC mappings and their selected bindings are active. The exact Sharadar
history manifest is bounded to 800 requests, 1 GiB and two hours; the exact
SEC missing-issuer acquisition manifest is bounded to 3,422 requests, 8 GiB and
two hours. There are no automatic retries. SEC acquisition is private while
Sharadar publishes, then complete source pairs will publish through their owner.
FMP's account policy and full Equibles associations remain pending. Unit files
alone do not establish installation, activation, or successful future execution.

See the [current rollout receipt](SELECTED_UNIVERSE_ROLLOUT_2026-09-09.md) for
mappings, finite manifests, actual checks, gaps, and subsequent dated activation
results. Current private evidence is `.local/universe-rollout-20260909`.

### September 9, 2026 UTC — observed selected-universe activation

At 21:08:41 UTC, one user-manager daemon reload loaded the reviewed expanded
SEC service behind the existing weekday 07:15 America/New_York timer. The manager
reported the selected SEC worker and no pending reload; the timer remained enabled
and waiting for September 10. No manual recurring-service execution was triggered.

At 21:48 UTC, the existing loopback Live Fetcher process was reloaded on port 8766.
HTTP health verified registry 2.79 and the status route returned successfully. This
is local read-only Inspector activation, not external exposure. Sharadar scheduling
is still uninstalled pending one explicitly held initial-history recovery request.
The additional SEC lookup-index migration remains isolated and unapplied while
its required full offline gate completes. Provider acquisition totals and all
remaining coverage gaps are recorded in the rollout receipt.

### September 10, 2026 UTC — verified SEC indexes and user-restarted WSL

The complete SEC index validation gate and independent review closed with passing
evidence for all 2,076 unique cases. The raw full run's two fixture failures and
exit 1 remain preserved alongside the 30 successful correction rechecks.
The reviewed 40-path patch was integrated at 02:52 UTC; company migration
0015_sec_completion_indexes applied at 02:56:58 UTC. Canonical postchecks at
02:58:29 UTC preserved all prior table counts, trigger bodies and migration rows,
with no foreign-key violation and zero provider requests. Registry is 2.80.0.

The user subsequently restarted the unresponsive WSL instance and requested
continuation. A 04:01 UTC observation confirmed the expanded SEC timer remained
enabled and waiting for 07:15 EDT; no recurring unit was manually triggered.
The exact retained-data continuation began afterward for the remaining 1,702
issuer pairs, zero requests and no repeated completed pairs, within the original
aggregate active-publication budget. Its initial private-file permission rejection
was corrected without changing bytes or making canonical writes.

The existing local read-only Inspector was restored at 04:04 UTC on the same
127.0.0.1:8766 endpoint; HTTP health reported registry 2.80.0. No external hosting
or exposure was added. Sharadar's single held recovery request and FMP account
policy remain pending; neither gains new request authority from the host restart.
See the rollout receipt for final publication counts and remaining provider gaps.


### September 10, 2026 UTC — SEC retained backfill completed with explicit gaps

All 1,711 planned missing issuer pairs were processed by 04:36:50 UTC:
1,669 new successful pairs and 42 explicit issuer gaps, zero unattempted pairs.
Including 513 retained issuers, coverage is 2,182 selected CIKs representing
2,204 securities. Two securities lack a SEC identity. Publication used zero
new requests and 1,494.107507 aggregate active seconds; original acquisition
charges are unchanged. Completed populations must not be repeated. The rollout
receipt records the 29 core-mapping gaps, TNET invalid fiscal year, eight
immutable metadata conflicts and four CompanyFacts 404s.

The independently reviewed durable metadata-hold correction was integrated at
05:07:22 UTC after 42 passing focused checks. It preserves canonical identity
and error boundaries without changing a registry, migration, binding, quota,
unit or cadence. Eight proven historical failure holds were seeded at 05:07:54
UTC: no provider request, canonical write or failed publisher retry. Private
hold records are keyed by canonical publication identity and retain exact
classification/source/audit evidence across manual and scheduled dates.

At 05:09:50 UTC, read-only checks verified all eight holds, unchanged SEC counts,
unchanged migration/failure audits and the integrated sources. The existing
expanded SEC timer was enabled and waiting for September 10 07:15 EDT, service
inactive/MainPID 0, no pending reload. No manual recurring execution occurred.
The loopback Live Fetcher status returned HTTP 200; no visual or future-run
claim is made. Sharadar scheduling remains uninstalled pending its single
held recovery decision; FMP policy and full Equibles transition remain pending.
See the current rollout receipt for the exact final evidence and limitations.


### September 10, 2026 UTC — selected daily-price backfill and FMP allowance

The user selected daily prices as the next individual dataset to backfill and
reported FMP Premium capacity of 750 calls/minute and 3.94 GB used out of 50 GB
in the rolling 30-day window. They subsequently explicitly replied **“Activate
the shared limiter”**. The shared FMP allowance is active at 250 ms minimum
request spacing, a local 30,000/day ceiling and 6,000 units reserved for normal
maintenance. These daily units are local controls, not a claimed provider daily
limit. The initial 5,000-unit reservation conservatively covers unledgered prior
traffic, including the early profile attempts; it is not measured account usage.

The frozen new-price population contains 1,734 selection members without an FMP
instrument identity. Its profile phase is capped at 1,734 GETs, 128 MiB and two
hours from the original start. The following historical-price phase is capped
at 13,872 GETs, 8 GiB and four hours, using non-overlapping windows from 1990
through September 9, 2026. Existing equity, ETF and index price populations are
excluded from this backfill. Each phase retains original responses and attempt
receipts, with no automatic provider retry.

The first configuration replacement failed on the private-file helper's mode
check. Automatic approval review then rejected the proposed shared activation;
the profile process was stopped. The subsequent explicit user approval above
resolved that activation gate. Of the first 122 local reservations, 121 responses
were retained and ARWR remained uncertain. A second pause at local reservation
152 was a backward WSL clock step before the FMP gate dispatched AUR. Both names
remain excluded from the untouched-name continuations. Original receipts remain
preserved. The continuation waits for a pre-dispatch clock rejection to settle
within the same request deadline; it does not repeat a dispatched provider GET.

Exact operational evidence is under
`data/.operations/collection/daily-price-backfill-20260910/`.
The daily-price binding remains prepared until the identity publication and
price-specific activation receipt exist. The existing market-close timer's
18:00 America/New_York cadence is retained. Other dataset bindings and timer
cadences are not expanded by the shared allowance activation.


### September 10, 2026 UTC — separate longest-price backfill and seven-day clock

The user's later explicit decision supersedes the preceding proposed
1990/new-names-only historical scope: use a dedicated backfill for the longest
available selected-equity history, then maintain the expanded universe with
only the trailing seven calendar days. The original proposal and attempt
receipts remain preserved. No history GET or expanded clock cutover had started
when this correction was made. The prior live implementation requested the
current session; the prepared selected route now explicitly requests end minus
six calendar days through end, with the unchanged five-row/64 KiB response
bounds. It cannot dispatch historical windows.

Profile acquisition completed with 1,734 local reservations, 1,731 HTTP 200
responses and 4,083,236 response bytes. ARWR remains uncertain. AUR and INTR
have proven pre-dispatch clock/slot exclusions and were not retried. The other
seven gaps are exact dotted share-class symbols with empty FMP profiles.
At 13:39:40 UTC, publication resolved 2,238 of 2,248 members, including two
reviewed retained class-B associations (BRK.B/BRK-B and BF.B/BF-B). It created
1,722 new instruments while legacy news/company/price rosters remained
630/519/630. Mapping receipt:
`data/.operations/collection/daily-price-backfill-20260910/mapping-activation.json`.

The dedicated historical worker is
`quant_data/operations/selected_price_backfill.py`, with the fixed-host receipt
adapter `.local/universe-rollout-20260909/longest_price_backfill_20260910.py`.
Its frozen preflight selects 1,722 new and 516 retained supported equities,
through September 9, 2026. Completed Stage 10 base/extension populations from
1990 onward are retained. Older extension requests for the 516 names end before
1990; no completed historical population is repeated. New names are requested
in non-overlapping windows of at most five years, continuing through the
retained provider IPO date and an older empty window. Nonempty older responses
extend the search further. The 1900 lower search guard is an explicit stop, not
a claim about the provider's earliest data. Hitting it with data, a request,
byte or time cap, or a failure leaves coverage incomplete. ETF/index histories
are not repeated; their existing live selections are retained.

The aggregate historical ceiling remains 13,872 GETs, 8 GiB and 14,400 seconds,
zero provider retries, using the existing FMP_API_KEY resolver, shared allowance,
`data/market.sqlite`, fixed physical store locks and replay-safe price publisher.
Each response is retained before progressive publication. The manual worker
has a start guard and immutable request/publication checkpoints; an interrupted
run does not silently restart. Canonical post-checks preserve original row
counts and verify published additions. Provider-window coverage and explicit
empty boundaries do not assert every exchange session is present.

The active daily-price binding permits this dedicated backfill. Expanded clock
execution additionally requires a private verified-history receipt matching the
selected price population and its completed result hash. Until that receipt
exists, normal market-close execution retains its legacy scope. The worker
writes it only after all supported targets complete and canonical checks pass;
identity/provider nonavailability remains explicit. Unrelated binding changes
do not invalidate the population receipt. The existing weekday 18:00
America/New_York cadence is unchanged; no manual recurring-unit trigger occurs.
Execution and validation receipts are under the same operation directory in
`longest-history/`; a preflight alone is not evidence that acquisition started.


At 13:58:12 UTC the dedicated history worker launched. It retained and
published 40 successful windows (39,939 price rows; five empty older windows)
before a backward WSL clock step stopped the queue before the next reservation.
No request was pending or failed. Canonical inspection verified ABT prices from
1985-01-02 through 1989-12-29. The selected workers now wait for real clock time
to catch up within the original monotonic deadline; they do not fabricate a
capture timestamp or retry an HTTP request. The explicit settled-checkpoint
continuation preserves all 40 requests and the original 14,400-second deadline,
8 GiB allowance and 13,872-request ceiling. Earlier result/audit bytes are
preserved alongside successor receipts. The live completion gate remains absent.
The baseline passed 59 focused checks; the clock/continuation revision passed
13 checks initially and its deadline-number correction passed the remaining
continuation check. No full suite or independent verification is claimed.

The settled continuation launched at 14:04:54 UTC with 13,832 requests
remaining and the unchanged original deadline. The handoff receipt records
a running worker, successful additional publications, and the still-absent
expanded-live completion gate. Historical backfill is not yet complete.


### September 10, 2026 UTC — 500/minute parallel historical-price acquisition

The user explicitly requested a 500-call/minute target and parallel ticker
batches for the existing finite daily-price history backfill. At 14:53:41 UTC,
the shared FMP allowance changed from 250 ms to 120 ms minimum spacing. Its
30,000/day local ceiling, 6,000 maintenance reserve and 8,402 existing charged
units were preserved. This numeric rate change does not increase the original
13,872-request, 8 GiB or 14,400-second historical limits, add provider retries,
expand ticker scope or change recurring-unit configuration. The original
monotonic deadline remains 46884.350574728.

The dedicated pipeline uses up to eight concurrent HTTP-only child processes,
a serial quota/response-retention parent and one independent canonical writer.
It retains existing account/store locks, immutable original responses and the
existing replay-safe price publisher. The producer pins identity validation
before starting the writer; it does not perform live immutable store reads
while publication is running. Actual throughput depends on provider latency,
retention and pacing overhead; 500/minute is the configured target, not a
claim of measured sustained throughput.

The serial worker had naturally stopped at 14:34:51 UTC on SENEB's HTTP 200
payload: two changePercent values were null under the existing strict numeric
contract. Its 1,786 charged requests, 1,785 published windows and 1,697,086
written rows remain retained. SENEB is held without coercion or retry.
The first parallel run started at 14:54:04 UTC. It retained and published 289
additional windows, then stopped after four later requests reached the shared
batch deadline without a retained response. UPST, UVSP, UVV and VCTR remain
uncertain and charged; no successful response or provider nonavailability is
asserted for them. The final settled total was 2,079 requests, 2,074 published
windows and 1,981,565 written rows, with 252 supported symbols at their terminal
history boundary. All original receipts are preserved in longest-history/parallel/.

The corrected adapter separates a ten-second request-admission slice from
response draining, with up to the existing 45-second request allowance afterward
(maximum approximately 55 seconds of batch account-lock occupancy plus local
retention/cleanup). Admission also requires enough time in the original overall
deadline. Clean batch boundaries continue only never-charged windows; uncertain
or systemic failures still stop acquisition. A fixed continuation preflight
reconciles every retained response/publication, rejects overlapping windows,
excludes the four uncertain symbols without refund or retry, and reserves their
64 MiB combined worst-case unretained bytes separately from measured bytes.
The prior SENEB validation gap remains held. The seven-day live activation gate
stays closed while any supported history target remains incomplete.

The scoped acceptance lane is TEST_STRATEGY section 4's authorized bounded
provider/store operation through established mechanisms. Independent review
corrected the initial overbroad full-suite classification: this delta does not
change shared account, identity, time, selection, physical locking, credential
or canonical publication contracts. The initial 15 focused checks and corrected
19 focused/adjacent checks passed. The supplementary full-suite process ended
without a completion summary and is unverified; it is not counted as passing.
Validation snapshots and command outputs are under
.local/universe-rollout-20260909/parallel-validation/. An immutable canonical
check at 15:03:36 UTC verified prior row preservation and all 1,981,565 published
additions across the 2,248-member mapping. This is not a claim of every exchange
session being present or of full history completion. New activation/continuation
receipts, rather than this preparation narrative, establish subsequent execution.


The corrected eight-download continuation launched at 15:05:20 UTC under
longest-history/parallel-continuation/. It made 688 additional charged requests:
all responses were retained, 687 windows were published, and DFTX was held for
strict payload validation without retry. No additional request became uncertain.
A deliberate next-batch barrier settled acquisition before any request in that
batch; the barrier-created parent directory mode caused a pre-dispatch
ConflictError. The original acquisition failure summary is preserved. The writer
finished all 17 retained batches by 15:09:59 UTC. Reconciliation therefore uses
exact immutable attempt/response/publication receipts, not the incomplete
acquisition summary. Cumulative counts are 2,767 charged requests, 2,761
published windows, 2,594,245 written rows and 352 complete supported histories.
Retained bytes total 576,725,333, with the separate 64 MiB reserve unchanged.

Measured download latency (median 1.72 seconds, 90th percentile 3.16 seconds)
and eight occupied HTTP slots explained sustained throughput near 220/minute.
The prepared capacity adjustment changes only the download ceiling to 24;
120 ms shared pacing, daily/reserved quota, one writer, ten-second admission,
full request draining and the original workload caps remain unchanged.
Twenty focused/adjacent checks passed in 23.215 seconds, including a slower
response fixture proving more than eight simultaneous requests remain paced.
The pool24-baseline.json snapshot and pool24-focused-result.json retain this
validation. A fresh immutable canonical audit at 15:14:20 UTC verified original
rows and all 2,594,245 additions. The six held symbols are DFTX, SENEB, UPST,
UVSP, UVV and VCTR; none is silently retried or marked complete. Subsequent
longest-history/parallel-pool24/ receipts establish whether this capacity
adjustment was actually launched.


Independent review verified the 24-download change, which launched at
15:16:26 UTC under longest-history/parallel-pool24/. Its initial observation
recorded 236 charged requests over 51.7 seconds (about 273/minute), no overlap
with prior attempted windows and no retry of held symbols. This did not
establish sustained 500/minute throughput. The run later stopped on a distinct
pre-ticket budget edge: durable reservation persistence crossed the planned
batch admission boundary before HLNE's HTTP child was constructed. Its final
267 charges comprise 266 retained/published windows and that one proven unsent
reservation. No new provider uncertainty or payload gap was added. The writer
settled at 15:18:27 UTC: cumulative 3,034 charges, 3,027 published windows,
2,843,531 written rows and 384 complete supported histories.

The final timing correction keeps the ten-second quota-admission slice, but
starts each admitted transport's existing 45-second allowance after durable
reservation work, capped by the original overall deadline. The planned
55-second drain estimate excludes local persistence/cleanup and is explicitly
not an independent hard request cutoff. The recorded prior-source combination
of invocation_budget, exactly one pending reservation, null account stopped
state and no transport-failure receipt identifies the pre-ticket branch for
the fixed HLNE key. Reconciliation preserves its charge and allows the untouched
2017-2021 window to continue without an HTTP retry. The six earlier held symbols
and their 64 MiB unretained-byte reserve remain unchanged.

All 21 focused/adjacent checks passed in 23.564 seconds, including a synthetic
10.25-second reservation-write delay. Independent source review found no blocker
in this correction or its fixed reconciliation driver, with the stated limit
that operational facts were verified by the integration owner. The immutable
canonical audit at 15:24:28 UTC verified all 2,843,531 additions and original
row preservation. The final continuation launched at 15:25:50 UTC under
longest-history/parallel-admitted/ with 10,838 request reservations remaining
and the unchanged original byte/time limits. Its launch, validation acceptance,
reconciliation and subsequent progress receipts establish current operation.
The seven-day live activation remains gated by completed supported history;
no recurring unit was manually triggered or changed by these continuations.


### September 10, 2026 UTC — resume after interrupted concurrent requests

The user asked to continue after the prior turn was interrupted. Live inspection
found the admitted-request worker had exited: its last requests were charged
near 15:28 UTC and 16 transport failures were retained at 22:59 UTC. The cause
of the host-time gap is not established. The original monotonic deadline had
not expired; it was not reset or extended. The settled result contained 3,501
charges, 3,478 published windows, 3,252,073 written rows and 454 completed
supported histories. An approved immutable audit at 23:08:36 UTC verified all
published additions and prior-row preservation.

The fixed resume driver reconciled 467 attempts from that run: 451 retained
responses were published and 16 interrupted responses remain unknown. Their
symbols are LSTR, NWPX, NYAX, OC, ODC, OGE, OGN, OHI, OLN, OMC, ONC, ONDS,
OPK, ORKA, OSIS and OSK. These requests remain charged and excluded from retry.
Their 256 MiB worst-case response bounds increase the separately accounted
unretained-byte reserve from 64 MiB to 320 MiB. The six prior held symbols remain
held, for 22 total. All original responses, receipts and failure evidence are
preserved. Shared pending/stopped state was cleared under its existing account
lock only after exact attempt matching and durable reconciliation receipts.

Independent source review and the exact operational preflight passed. The core
dispatcher, pipeline, tests and earlier drivers match the reviewed 21-test
baseline, so those checks were not unnecessarily repeated. This does not claim
an exhaustive suite pass or sustained 500 calls/minute. The unchanged parallel
mechanism keeps 24 download slots, shared 120 ms pacing and one canonical writer.
Observed earlier throughput remained around 200 calls/minute because request
latency and batch draining reduced sustained throughput below the configured
500/minute ceiling.

The continuation launched at 23:09:59 UTC under
longest-history/parallel-resume/ with 10,371 reservations remaining under the
original 13,872-request cap, unchanged 8 GiB aggregate cap and original
46884.350574728 monotonic deadline. Its reconciliation, validation acceptance,
launch and progress receipts establish execution. No HTTP request was retried
and no recurring unit was manually triggered. The live price window remains
seven calendar days, with expanded activation gated on completed history.


### September 10, 2026 UTC — completing held price history

The user explicitly asked to finish price history after the resumed-run handoff
identified held windows. The main acquisition/publication continues within its
original allocation. A finite recovery plan lists one recovery GET for each of
20 interrupted windows; these are uncompleted populations, and their original
charges and worst-case byte reservations remain retained. No recovery request
has executed at this preparation point. Any recovery and newly encountered
older windows must fit the original remaining aggregate limits.

A bounded historical-adapter correction now accepts literal null changePercent
without relaxing mandatory OHLCV, shape, symbol, date, other ancillary values,
duplicate dates or window bounds. This ancillary field is absent from canonical
price columns. Its null remains null in historical semantic mapping; original
response bytes and source-row links are preserved. Shared Stage10/Stage12
parsers, canonical schema and normal live behavior are unchanged. Thirty-one
focused historical/pipeline/live-price/quota checks passed in 53.135 seconds;
independent review verified the scoped change with stated limitations. The
saved SENEB response now validates all 1,119 rows, including literal null
percentages on July15 and July18,2024, with zero new provider calls or canonical
writes during that verification. Actual publication awaits the existing writer.

DFTX reports open1.41 above high1.35 on November9,2018; RCAT reports open36 above
high34.8 on October24,2017. BNY returns two conflicting rows dated March5,1974
(one close1.56/volume0 and one close1.57/volume38471). These remain explicit
provider-data conflicts, not null-percentage cases. No value is invented,
selected from the duplicate pair or silently dropped. The original responses
remain intact for provider recovery and a subsequent data-quality decision.

The main acquisition settled at 23:34:46 UTC with 11,143 aggregate reservations,
2,115,458,668 received bytes, 11,117 validated windows and 2,213 histories at
the provider boundary. Its final held count is 25 because HE's 1987-1991
response has 31 inconsistent OHLC rows in 1987. The fixed manual recovery plan
was therefore extended before execution from 23 to exactly 24 GETs: twenty
interrupted windows and one correction check for each of BNY, DFTX, HE and RCAT.
No canonical connection was opened by this network-only recovery while the
existing single writer continued.

Independent source review found no blockers; the integration owner verified
terminal main acquisition, exact held population, remaining original limits,
and clear shared pending/stopped allowance at 120 ms spacing. The recovery
finished at 23:40:13 UTC: all 24 responses retained, all twenty interrupted
windows validated, four provider conflicts unchanged, zero automatic retries
and zero fresh uncertain responses. Aggregate usage is 11,167 reservations,
2,121,303,988 received bytes plus the original 335,544,320-byte uncertainty
reserve. None of the old charges/reserves was refunded. All original caps and
the monotonic deadline remain unchanged. Receipts live under
longest-history/manual-recovery/; the verified conflict inventory identifies
34 affected dates. The user has been asked to decide the treatment of these
known data gaps before any change to the strict completed-history live gate.
Recovery publication and continuation of untouched older windows are prepared;
this record does not yet claim their execution or final history completion.


The main writer settled at 23:55:05 UTC with all 11,117 valid windows published,
9,580,288 new rows and no publication failure. The first finishing attempt
stopped during its 90-second read-only grouped audit, before recovery publication
or older-history requests. A query-only correction uses equivalent indexed
per-mapping counts/boundaries. Isolated alias, missingness and filter checks
passed; independent source review found no blocker. Original limits were not
extended, and the failed read caused no canonical mutation or provider request.

At September 11 00:02:06 UTC the actual immutable main audit passed. All 20 valid
recovery windows plus SENEB's original modern response were then published
(22,714 additional rows); a second immutable audit passed. The final untouched
older-history continuation made 117 new requests, retaining 116 valid windows
and one new held SENEB 1987-1991 response. That response contains 211 OHLC
conflicts. No further automatic or manual correction GET was made for it.

The final writer and immutable audit settled by 00:03:20 UTC: 2,233 of 2,238
supported histories complete, all 2,238 supported identities have started,
11,254 published windows, 9,709,999 new canonical rows, 11,284 aggregate
reservations, 2,144,965,109 received bytes, and the unchanged 335,544,320-byte
uncertainty reserve. All owned workers exited. Shared FMP pending/stopped state
is clear at 120 ms spacing. BNY, DFTX, HE, RCAT and SENEB remain held with 245
observed conflicting dates; their held windows and older unvisited ranges are
not claimed complete. Ten unresolved source identities remain outside the
2,238 supported population. No completion/expanded-live activation marker was
written; the seven-day window and recurring units were unchanged. The explicit
data-gap disposition question remains pending. Detailed final evidence and
remaining scope are in PRICE_HISTORY_BACKFILL_2026-09-10.md and the operation's
recovered-history/completion-review.json; this is completion with known gaps,
not full-universe historical completion.

## September 11, 2026 UTC — expanded Equibles transcript backfill activated

The user requested the available-history Equibles transcript backfill for the
2,248-member Major Index Liquid selection, initially reporting 100,000 API
calls/day. A fresh response on the existing key still reported 10,000/day.
The user explicitly chose "Proceed at the verified 10,000/day limit", then
approved updating the existing daily job to continue this finite history walk
at 00:10 UTC, at most 10,000 shared calls/day and six hours/run until complete.
That later approval clears the automatic review rejection of the initially
proposed recurring-service update. No automatic failure retries were authorized.

The exact selected membership is
`collection_snapshot_18a66dfbec3a1ed017c87e9dabda9ccb`.
Twelve first-time selected-symbol identity batches (1,281,620 bytes, no retries)
produced the Equibles mapping
`collection_mapping_934f42c1fddbad8e0742619d9d4eaa18`:
2,222 exact provider-symbol/instrument associations and 26 explicit identity
gaps. Four dot/dash spellings were reviewed against returned provider fields
and the existing same-member FMP instruments. Secondary listings omitted by
the screener were not silently replaced with a sibling share class. Details
and gap symbols are in the dated activation section of
[EQUIBLES_TRANSCRIPT_BACKFILL_2026-09-07.md](EQUIBLES_TRANSCRIPT_BACKFILL_2026-09-07.md).

At 02:35:08 UTC the existing publisher appended the mapping to market.sqlite;
the shared job lock protected checkpoint conversion and the Equibles-only
binding activation. The original checkpoint, all 445 existing transcripts,
17 completed histories, AIZ partial history, response cache and 113 current-day
charges were preserved. Immutable postchecks verified all 445 canonical
records and page hashes, mapping counts and unchanged other bindings.

The narrower operating controller enforces 10,000 total shared charges/day
even though the existing versioned entitlement policy supports 100,000.
Each inner invocation retains its 1,000-request, 400 MiB, one-hour limits.
The daily controller allows at most 24 invocations, 4 GiB and six hours,
stops at the current UTC-day boundary or earlier provider allowance, and
continues only new unfinished work. Existing one-second pacing, 30-second
request deadlines, 8 MiB responses and failure/404 handling remain in effect.
No new catalogue epoch or model-analysis workload was activated.

The existing service now uses the controller when the Equibles binding is
active. Its timeout is 365 minutes; the timer remains enabled at 00:10 UTC.
A configuration reload confirmed the six-hour-five-minute timeout and next
clock execution on September 12 at 00:10 UTC. No manual recurring-unit start
was used. Today's separate dated allocation started with at most 9,887 new
requests, six hours and 4 GiB, no retry and no next-day spillover.

At 02:40:33 UTC the running canonical postcheck found 534 transcripts across
22 symbols, including 89 newly published calls since activation. Twenty-one
selected histories were complete and ALB was underway; no blocking error
was recorded. This is activation and running-progress evidence, not a claim
that all 2,222 histories have finished.

Validation: 53 existing focused Equibles tests passed before activation;
12 installed controller/entrypoint tests passed in 0.803 seconds; unit
verification passed. Independent review accepted the mapping, preservation,
deadline/day guards and bounded controller. The full suite was not repeated:
existing core code matches the prior final-v2 baseline, and this change adds
the bounded controller, entrypoint routing and service timeout. Unrelated
concurrent FMP/price edits were preserved and shared-config cutover coordinated.

Retained operational evidence:
`data/.operations/equibles-expansion-20260911/`, especially
`authorization.json`, `identity-result.json`, `mapping-prepared.json`,
`activation-result.json`, `activation-postcheck.json`,
`service-installed.json`, `validation-receipt.json`,
`backfill-started.json`, and `running-canonical-postcheck.json`.
The dated process writes `backfill-result.json` when it ends. Ongoing inner
progress remains in `data/.operations/equibles-transcripts/status.json`;
the aggregate daily allocation uses `daily-status.json`.


<a id="recorded-selected-company"></a>

## Selected company collectors — explicit September 11, 2026 expansion

The user explicitly authorized one missing-history backfill and live collection
for earnings, FMP statements including full as-reported statements, analyst
estimates, recommendations/ratings, price targets and product-revenue segments,
then selected **all six groups every weekday**. This newer scoped decision
supersedes their prepared-only endpoint disposition. It does not add other
collector groups or authorize repeating completed historical populations.

At 02:53 UTC the four applicable company bindings were activated while
preserving daily prices, active Equibles and every other binding. The local
FMP daily ceiling changed from 30,000 to 60,000; 120 ms pacing, the 6,000
maintenance reserve, effective date, initial charge and all recorded usage
were preserved under the existing account lock. The exact cutover receipt is
`data/.operations/collection/company-selected/allowance-activation-20260911.json`;
configuration SHA-256 after cutover is
`33b80fdcee64a2ae09ce8646a4a54fba7576f9018c523763bd89dbad37b0e021`.
This local allowance change does not purchase a different provider plan.

The frozen backfill targets 2,201 issuer-ready symbols from 2,248 selected
securities, with 47 explicit issuer/CIK gaps. It excludes 4,646 retained
endpoint/period scopes and permits 37,173 initial GETs, at most 67,539 total
GETs, 8 GiB and 72 hours from the original worker start. Provider retries are
zero. It started at 02:54 UTC; its first 19 responses were retained, with two
as-reported date encodings corrected from original bytes without another GET.
Original evidence and explicit continuation receipts stay under
`data/.operations/collection/company-selected/backfill-20260911/`.
All continuations retain the original aggregate caps and monotonic deadline;
completed or uncertain GETs are never silently repeated.

The new `quant-data-selected-company-refresh.timer` is authorized for weekdays
at 20:00 America/New_York, without catch-up or automatic restart. As of this
entry it is **not installed or enabled**. Its live activation remains gated
on the post-backfill audit and matching selected population. See
[collector scope, bounds and validation](SELECTED_COMPANY_COLLECTORS_2026-09-11.md)
for the implementation and actual progress; this entry does not claim the
backfill or first scheduled run is complete.


## September 11, 2026 UTC - Equibles parallel continuation running

The user explicitly requested multiple concurrent Equibles API callers. This
activates up to four network callers with a global one-second start interval,
under the existing single quota/checkpoint owner and serial canonical publisher.
The previously approved 10,000 shared calls/day, six-hour, 4 GiB, 24-invocation
allocation and zero automatic failure retries remain in force. The selected
2,222 mapped symbols and 26 identity gaps are unchanged. Existing physical
store locks and identity/time/schema contracts are reused. Lower fresh quota
headers cancel waiting calls; saved remaining never increases from response
reordering. Unknown attempts stay held, and verified 429s defer at zero quota.

The prior process had stopped at charged attempt 382 after a 0.361583-second
host-clock inversion. Its complete AMD 2020 Q2 response was recovered at
03:28:44.967462 UTC without another provider request. The original receipt and
all usage fields were preserved, with explicit later local re-observation
proof supplying conservative canonical availability. The prior 702 complete
transcripts became 703. The parallel continuation started at 03:29:09 UTC,
limited to the original unused 9,618 calls, 4,281,206,196 bytes, 23 invocations
and original 08:37:30.771799 UTC deadline; it adds no allocation or retry.

At 03:30:34 UTC the process was running with 761 complete transcripts across
31 symbols, 30 completed histories and AMGN underway. All prior 702 capture
records/page hashes were preserved; 59 new bundles and retained raw bytes
validated, with zero transcript foreign-key violations. The first 61 retained
parallel responses were HTTP 200, averaging 43.75 calls/min over 82.28 seconds;
the observed overlap peak was two requests within the four-caller ceiling.
Full expanded history remains in progress.

The active entrypoint uses this controller on subsequent normal clock runs.
The actual `quant-data-equibles-transcripts.timer` is enabled at 00:10 UTC;
its service is inactive during the separate dated continuation, with unchanged
365-minute timeout and no automatic restart. No recurring unit, cadence,
binding or credential was changed by this parallel activation.

The 36 focused checks and 53 adjacent compatibility checks passed. Independent
review found no remaining blocker after bounded corrections and six memory-only
boundary probes. The full suite was not repeated for the bounded controller;
the runtime audit used the established quiet immutable procedure. Exact
source hashes, recovery, launch, review and running audit receipts are under
`data/.operations/equibles-parallel-20260911/`. Implementation, preservation,
quota and timestamp details are in
[EQUIBLES_TRANSCRIPT_BACKFILL_2026-09-07.md](EQUIBLES_TRANSCRIPT_BACKFILL_2026-09-07.md#september-11-2026-utc---parallel-acquisition-activated).


## September 11, 2026 UTC — expanded current-news preparation

The user requested news implementation for the expanded universe. The prepared
current-news scope is 2,248 selected securities, 96 retained ETFs and 15 index
targets, using the existing hourly :10 UTC timer. At most 47 source-evidenced
Alpaca symbol batches plus nine unchanged shared-feed requests are planned per
normal invocation. Historical news backfill is not included.

The exact-symbol mapper, whole-roster checks and request-start time reservation
are implemented. Twenty focused and 22 compatibility tests passed, with a fresh
independent source review and ten overlapping memory-only checks. The existing
96 ETF identities reuse original FMP capture bytes; no new FMP request is needed.

A single read-only Alpaca asset-catalogue lookup, capped at 8 MiB and 60 seconds
with no retry, is awaiting explicit finite approval. No lookup, canonical mapping
publication, news binding activation, manual news fetch or unit change occurred
in this preparation. The news binding remains prepared. Actual activation must
be evidenced by data/.operations/news-expansion-20260911/activation-result.json.
See [scope, limits and validation](NEWS_UNIVERSE_EXPANSION_2026-09-11.md).


### Selected company completion worker — 2026-09-11 04:09 UTC

The user-authorized six-group expansion continues under the original finite
backfill and weekday-refresh bounds above. The coordinated immutable-reader
correction and exact retained-response recovery passed focused and independent
gates. All 42 interrupted responses were recovered without new GETs. A reviewed
one-time completion worker now waits for the complete settled backfill, verifies
canonical rows and both private/canonical source hashes, and only then activates
`quant-data-selected-company-refresh.timer` at Monday–Friday 20:00 New York.
It adds no provider request, retry, recurring Codex automation or deadline.
The timer was not yet activated at this observation. Actual activation evidence,
when generated by that sequence, is
`data/.operations/collection/company-selected/weekday-activation.json`; the
matching `live-activation.json` pins the verified population and audit hash.
The local Live Fetcher on port 8766 was reloaded and its health/status routes
returned 200 with the new selected-company row. Detailed dated evidence is in
[the collector rollout](SELECTED_COMPANY_COLLECTORS_2026-09-11.md).


## September 11, 2026 UTC — selected price history completed and daily refresh active

The user-authorized continuation settled all **2,248 selected FMP histories**
and resolved the ten remaining provider mappings, preserving the previous
2,238 mappings and existing history. The five held histories completed with
**1,231 quarantined dates** across BNY, DFTX, HE, RCAT and SENEB. Original
responses remain intact; clean subranges were separate original responses.
Every symbol has canonical prices and a terminal provider-history boundary;
no full exchange-session completeness claim is made. The combined backfill
added **9,783,014 rows through September 9**, including 73,015 in this
continuation. No selected history remains pending.

This continuation used 10 profile GETs and 988 history GETs, receiving
17,153,094 bytes within its original one-hour and 1 GiB bounds. Only eight
of the separately approved 200 additional BF.A/MOG.A requests were needed.
Automatic retries were zero; original charges and the prior 335,544,320-byte
uncertainty reserve remain preserved. Provider execution is finished; this
entry grants no repeat population or additional provider work.

At **05:41:58 UTC**, the reviewed zero-GET activation wrote the quality and
fresh immutable-audit receipts, then history completion, then the live marker
last. The first bounded audit timed out with no activation; an unchanged
driver rerun completed in 6.149 seconds. At 05:42:33 UTC the actual gate and
current population verified: **2,248 equities plus 111 retained ETF/index
instruments**, zero mapping gaps, 2,359 distinct daily targets and a latest
**seven-calendar-day window**. The existing user market-close timer was
loaded, active and enabled for weekdays at **18:00 America/New_York**, next
September 11 at 18:00 EDT. No recurring unit was installed, restarted or
manually invoked. The first expanded normal clock run is not yet observed.

Fifty-five focused checks and the scoped full-suite gate passed: **2,166
distinct cases reconciled**, zero missing or unresolved cases, with independent
acceptance. Raw interrupted invocations and corrected failures are preserved;
they are not represented as clean full-suite exits. Unrelated evolving
Equibles parallel, news and Inspector features remain outside this claim.
The repaired limiter status was shared only with the user-authorized other
task; shared allowance charges and concurrent company settings were retained.

Operational evidence is under
`data/.operations/collection/price-completion-20260911/`, particularly
`activation-validation.json`, `resumed-full-suite-validation.json`,
`completion-and-activation.json` and `activation-verification.json`. The
fixed hash-linked gate files are under
`data/.operations/collection/daily-prices/`: `history-quarantine.json`,
`history-canonical-audit.json`, `history-completion.json` and
`live-activation.json`. See [the completion report](PRICE_HISTORY_COMPLETION_2026-09-11.md)
for exact mappings, quarantine, validation and activation evidence.


## September 11, 2026 UTC — expanded current-news activated

Following the prepared implementation above, the user explicitly approved the
single Alpaca asset-catalogue request (8 MiB / 60 seconds / no retry) and news
activation. The request completed once at 13:12:26 UTC with HTTP 200 and
6,454,425 bytes; no retry or further provider request was used for cutover.

The news binding became active at 13:13:23 UTC. The existing hourly :10 UTC
batch now selects all 2,248 equities plus retained ETF/index matching: 2,359
matching instruments, with 2,343 exact Alpaca symbols across 47 batches. All
2,248 equities and 95 retained ETFs resolved; IRBO is an explicit catalogue gap,
and the 15 index targets remain unsupported Alpaca request symbols. Nine
shared-feed requests stay singleton (at most 56 requests per normal invocation).
This later scoped user decision supersedes the former 519-equity news scope.

Existing publishers stored the independently pinned retained ETF membership and
provider mappings. Existing instruments, selected membership and selected FMP
mapping hashes were unchanged; collection foreign-key checks passed. Only
news.mode changed in the binding file. The installed news timer remains
enabled/active/waiting with its next normal slot at 14:10 UTC (10:10 Toronto).
There was no unit mutation, manual news fetch or historical backfill. The first
expanded scheduled run has not yet been observed; current pages do not establish
complete historical or time-window coverage.

Evidence: data/.operations/news-expansion-20260911/activation-result.json and
post-activation-check.json. Scope and the 45 passing offline checks plus fresh
independent source review are detailed in
[NEWS_UNIVERSE_EXPANSION_2026-09-11.md](NEWS_UNIVERSE_EXPANSION_2026-09-11.md).


## September 11, 2026 UTC - Equibles local lock-timeout recovery

Investigation of the user's reported stop found a five-second physical company
store lock timeout at 04:58:53 UTC, while publishing DRI 2026 Q4. The API batch
had settled successfully; 3,613 shared calls were charged and 6,387 remained.
The stop was saved as a persistent publication block, which a later parallel
invocation would not automatically clear. The competing historical lock holder
is not identifiable from the retained evidence.

The domain-local publisher now uses the existing lock API with up to 60 seconds
of waiting for paid calls, always bounded by the invocation deadline. Provider
retry policy, locking primitives, canonical semantics, shared quota and recurring
units are unchanged. At 13:20 UTC the exact retained Q4 response was published
without any GET or quota change; its original capture timestamp was preserved.
The immutable postcheck found 3,812 transcripts and preserved all 3,811 prior
capture records/page hashes. Cached-only progress completed DRI and advanced
to DTE: 151 histories complete, no saved block, zero transcript foreign-key
violations. Sixty-one focused checks passed; the full suite was not repeated.

The earlier manual cutoff of 08:37:30.771799 UTC has expired. A new manual
continuation capped at 6,387 additional calls and three hours is awaiting user
approval; no such network run was started during the investigation/recovery.
The already approved 00:10 UTC timer remains enabled and can use the recovered
checkpoint on its next normal clock execution. Evidence and later authorization,
if granted, are under `data/.operations/equibles-lock-stop-20260911/`. See the
dated section in [the transcript contract](EQUIBLES_TRANSCRIPT_BACKFILL_2026-09-07.md)
for exact capture, quota, validation and recovery details.


### Equibles restart explicitly approved - September 11, 2026, 13:31 UTC

The user's subsequent "pls restart the equibles backfill process" accepts the
prepared finite continuation after the lock recovery. It started at
13:31:36.310918 UTC (PID 216297), limited to 6,387 additional shared calls,
three hours ending 16:31:36.310918 UTC, four callers, the 10,000/day ceiling,
zero API retries and the original unused 4,111,264,504 bytes/19 invocations.
This new explicit scope supersedes the pending-restart status above; it does
not refund earlier charges or change the recurring timer.

At 13:32:27 UTC the immutable audit verified 3,842 transcripts, 30 added since
restart, 152 completed histories, DUK underway and no blocking error. The
first 34 responses were HTTP 200. Prior captures/page hashes and the recovered
DRI call were preserved; new complete bundles validated with zero transcript
foreign-key violations. The shared ledger showed 3,649 charges and 6,351
remaining. Tested source hashes are unchanged, so the prior 61-test focused
result was reused. The approved daily timer remains enabled at 00:10 UTC.

Authorization, start and running audit receipts are under
`data/.operations/equibles-lock-stop-20260911/`; the dated worker writes
`resume-result.json` on completion. This entry does not claim full history
completion. See the [transcript record](EQUIBLES_TRANSCRIPT_BACKFILL_2026-09-07.md)
for exact bounds and preservation evidence.


<a id="recorded-company-actions"></a>

## September 11, 2026 — selected dividends/splits history and weekday activation

The explicit request to implement the two outstanding dividend/split expansions
supersedes the original action-roster ceiling for this lane of the existing
19:00 weekday company refresh. A finite 3,394-GET run completed with 3,350
canonical successes and 44 duplicate-dividend source rejections, zero retries or
pending work, and all prior action rows preserved. The exact 4,402 assessed scopes
cover 2,201 issuer-ready securities; 47 selected securities remain identity gaps.

At 2026-09-11T15:25:56Z an audited local actions receipt enabled monitoring with
those explicit source gaps. The shared binding configuration and FMP allowance
were unchanged. This actions-only receipt is the authority for the prepared
binding's adoption by this job; it does not globally activate other collectors.
The action lane is bounded to 4,402 GETs, 512 MiB and two hours; legacy annual
estimates remain separate. The enabled weekday 19:00 timer was verified after a
user-manager reload; the service timeout is now 151 minutes. No service was
manually invoked, and the first expanded clock run has not been observed.

Do not repeat the completed successful action population. Preserve the original
exit-75 backfill result and its 44 rejected-response records. Full history for
those responses and the 47 identity gaps is not claimed. Detailed limits,
canonical preservation, validation and evidence paths are in
[the dividends/splits rollout record](DIVIDENDS_SPLITS_EXPANSION_2026-09-11.md).


### Selected company collector index validation and rollback - 2026-09-11T18:35:11.766857+00:00

Within the explicitly requested six-group expansion and weekday refresh, the
additive lookup-index candidate completed its required full-run reconciliation
and independent evidence gate. The original native exit 1 and all 53 findings
are preserved; 90 corrective cases passed, accounting for all 2,205 unique cases.

The first index application at 18:10 UTC rolled back atomically after the
mandatory whole-company foreign-key check exceeded a 20-second SQL cap.
No source integration or new provider request occurred. The company ledger
remains at 15, both new indexes are absent, and the captured rows, snapshots and
triggers are unchanged. All 39 source stages are retained for explicit recovery.

A separate single-use recovery is prepared with a 3,600-second SQL cap,
30-second reconciliation allowance, 60-second publication reserve and
4,020-second outer timeout. It retains the accepted migration, mandatory full
foreign-key check, physical locks, source pins, original boot/deadline and all
GET/byte limits. Independent acceptance remains required before this recovery.
Evidence: .local/company-collectors-expansion-20260911/queued-recovery-preflight.json
and performance-application-failure-264079.json. The six-group weekday timer
remains uninstalled and the provider backfill remains paused at 7,696 completed
requests / 29,939 pending units. This dated entry does not assert later host state.

At 2026-09-11T18:37:54.669872+00:00 the separate recovery started after
independent acceptance (queued-recovery-independent-review.json). Supervisor
PID 266290 and application PID 266327 were observed running, with the recovery
started marker present. This is launch evidence, not migration completion;
no new provider request or weekday activation is claimed by this entry.


### Selected company continuation after committed indexes — September 11, 20:26 UTC

The additive company lookup indexes committed at 18:43:23 UTC and all 39
accepted source updates were integrated. The original data counts and triggers
were preserved. The subsequent dashboard timeout stopped the supervisor before
provider work; a later bounded loopback check returned HTTP 200 on both routes.
No further migration or Inspector restart was performed.

The existing continuation-4 and finite audit/activation finalizer were launched
at 20:26:01 UTC as PIDs 268782 and 268783, within the original finite scope.
The first 64 resumed responses were published successfully. Original request
and byte caps, the 72-hour deadline and zero provider retries remain intact.
The new weekday timer is still pending completion and the canonical audit.
Evidence: post-index-continuation-launch.json, inspector-post-index-check-20260911T2024.json
and performance-application-result.json under the existing private rollout directory.


At 2026-09-11T20:40:29.746897+00:00, selected-company continuation 5 launched
as PID 271114 with finite audit/activation finalizer PID 271115. A bounded
capture-order correction handles two share classes of the same issuer without
changing identity/time semantics. All 27 focused checks passed. The 43 retained
responses from the stopped batch were settled with zero provider requests;
one already superseded BATRK annual cash-flow response remains rejected with
its original raw evidence. All 384 continuation-4 requests are preserved.
Continuation 5 starts with 8,061 requests / 29,555 pending units, retaining the
original 67,539 aggregate GET cap, 8 GiB cap, 72-hour deadline and zero retries.
The weekday timer remains pending the completed canonical audit.

## September 11, 2026 UTC - evening Equibles quota continuation

The user explicitly requested resuming the unfinished Equibles backfill to use
today's 10,000-call allowance. This is a new dated continuation, ending before
September 12 00:00 UTC (September 11 20:00 Toronto), with at most 2,782 additional
charged calls, four callers, no automatic provider retries, and the unused
3,928,144,568-byte / 15-invocation allocation. The original 7,218 charges and all
2,222 mapped subjects / 26 identity gaps are preserved. The existing credential
resolver, company publisher, registry, canonical schema and daily timer are unchanged.

The earlier continuation stopped at 15:01:37 UTC on LUV 2023 Q2. Its retained
HTTP 200 response has the exact expected event/ticker/fiscal identity but zero
turns, totalTurnCount=0, offset=0, hasMore=false and empty data/corrections arrays.
This is an observed empty transcript, not an unknown network attempt. Its hash is
55c9fd7ad783a4c6e6db1e22f2e8e3e3896d277c038f6c705e863ea2096a1ae3.
The existing September 8 instruction to skip missing quarters was applied only
to this exact response: the checkpoint records provider_returned_empty_transcript,
HTTP 200, original observation time/hash and a recovery reference. No empty
canonical transcript was published and no provider request was repeated.
The two subsequent reservations have exact not-dispatched markers; their charges
were retained.

At 21:24:00 UTC the existing publisher recovered retained valid LUV 2023 Q1,
then the exact checkpoint advanced past the empty Q2 to Q3. Recovery used zero
GETs, preserved all 7,273 prior captures/page manifests and the migration ledger,
and brought canonical transcripts to 7,274 without changing quota. The original
blocked checkpoint, acquisition journal and response evidence remain preserved.
The shared parser and parallel failure policy were not broadened; a different
malformed or uncertain response still stops for review.

The dated worker started at 21:24:23 UTC as PID 281327. At 21:25:50 UTC the
immutable running audit verified 7,328 canonical transcripts, 55 new complete
bundles including the retained Q1 recovery, preserved all 7,273 prior captures
and page manifests, and found zero transcript foreign-key violations. Raw
hashes, retained bytes, complete bundles and ingestion lineage passed. The first
56 newly retained responses were HTTP 200. The ledger held 7,278 charges /
2,722 remaining, 295 histories were complete and LYB was in progress.

All 61 focused publisher/collector/parallel/daily tests passed in 13.047 seconds.
The exact recovery probe and seven rejecting boundary probes passed without
credentials or provider requests. The full suite was not repeated. No shared
implementation, unit, registry, migration, commit or push changed in this task.
This is confirmed resume/progress evidence, not completed daily quota or history.

Evidence: data/.operations/equibles-quota-resume-20260911-evening/
contains authorization.json, preflight.json, checkpoint-before.json,
journal-before.json, canonical-before.json, recovery-result.json,
recovery-postcheck.json, started.json and running-postcheck.json.
The dated worker writes result.json on completion and run.log during execution.
Its one-use helper is .local/equibles_quota_resume_evening_20260911.py.


### Six FMP company groups — corrected continuation, September 11, 21:44 UTC

The user's renewed request to finish the six selected-company datasets and
start backfill authorized continuation of the existing finite population.
No provider population or recurring schedule was broadened. The implementation
now handles the observed unambiguous as-reported date encodings; fourteen
previously rejected retained responses published as 187 source rows with zero
new GETs and original capture times.

After independent review and zero-GET preflight, continuation 6 launched at
21:43:23 UTC (collector start 21:43:37 UTC), PID 286119. It preserves all settled
requests and excludes exactly the charged uncertain CHEF annual full-as-reported
attempt 2026-09-11-18878. Its original attempt and 8 MiB uncertainty reserve
remain; the exact shared guard transition preserved daily usage 18,879,
last-request time and policy. No uncertain request was retried or refunded.

The operation retains the original 67,539-request, 8 GiB, 72-hour ceilings and
existing FMP key/company store. The effective narrowed request ceiling plus
pilot and held charge is conservatively 67,521. Existing 60,000/day local shared
allowance, 6,000 maintenance reserve, and 120 ms pacing are unchanged.

At 21:44:50 UTC, the running worker had completed 128 new requests; 23,207 units
remained queued. Bounded canonical checks verified fresh publications and raw
hashes. Three source rejections, one held uncertain request and 47 company
identity gaps remain explicit. The original full-workload audit and weekday
activation gate remains closed; no recurring unit or finalizer was started.

Validation: 35 focused source/statement/collector tests, eight hold checks,
independent guard/recovery review and final fourteen-result overlay comparison.
Details and dated evidence are in
[the selected-company contract](SELECTED_COMPANY_COLLECTORS_2026-09-11.md#date-encoding-correction-recovery-and-actual-restart)
and .local/company-collectors-expansion-20260911/continuation-6-postlaunch-verified.json.

## September 11, 2026 UTC - empty-quarter continuation implemented and restarted

The user explicitly requested updating the collector so an empty quarter advances
to the next quarter, then restarting the backfill. This supersedes the earlier
one-response-only LUV recovery boundary for this precise empty-quarter case.

The private serial collector now recognizes a successful transcript response
with the expected ticker, event ID and integer fiscal labels, an empty data
array, zero turn and total-turn counts, offset zero and hasMore=false. It
records provider_returned_empty_transcript with the real HTTP 200 status,
original response hash/time and fiscal identity, then atomically advances the
cursor using the established unavailable-quarter mechanism. It does not publish
an empty canonical transcript or claim complete coverage. The parallel response
validator accepts the same bounded empty case, including retained-batch recovery.
The strict canonical nonempty parser/publisher is unchanged. Wrong identities,
missing/invalid quota headers, contradictory pagination and empty later pages
remain held failures; there is no automatic failed-request retry.

The prior evening process ended at 21:54:31 UTC after 1,195 additional charges,
with 8,413 used / 1,587 remaining, 8,423 transcripts and 338 completed histories.
NI 2024 Q4 had returned the same valid zero-turn envelope; its two preceding
responses were complete and the following reservation was proven undispatched.

At 23:37:52 UTC the updated existing recovery path settled the retained NI
batch without any new GET, published its two complete neighboring transcripts,
recorded the Q4 gap and advanced to NI 2025 Q1. Prior charges and the conservative
remaining allowance were preserved. The source/checkpoint/journal baselines and
original responses remain retained.

A separate dated worker started at 23:38:32 UTC as PID 309356, using four callers,
at most 1,587 additional charges, 3,866,477,331 unused bytes and 13 invocations.
It cannot cross September 12 00:00 UTC (September 11 20:00 Toronto) or the existing
10,000 shared-call daily ceiling. The existing credential resolver, company
publisher, locks, binding and daily timer are unchanged. Future normal clock
runs load the updated collector; no timer/service was manually triggered or
reconfigured.

All 69 focused empty-quarter, publisher/collector, parallel and daily-controller
checks passed in 13.599 seconds. The original run's two new-test expectation
failures remain in focused-tests.log; corrected expectations use the controlled
fixture file time and allow valid retained-response quota-header reconciliation
while proving unchanged charges and conservative remaining. Regression tests
cover ordered skips, retained evidence, replay, crash recovery, existing blocked
batches, canonical rejection of empty calls, malformed data, partial-page stops
and quota-header failures. The full suite was not repeated for this private
collector change; canonical/schema/shared-contract code did not change.

At 23:39:24 UTC the immutable running audit verified 8,451 canonical transcripts,
all 8,423 prior captures/page manifests preserved, 28 added complete bundles
including the two zero-GET recoveries, and zero transcript foreign-key violations.
New bytes, hashes, complete bundles and lineage passed; migrations were unchanged.
NI completed with its explicit gap and NKE was in progress. The first 29 new
responses were HTTP 200; the ledger was 8,444 charged / 1,556 remaining.
This is verified running evidence, not completed daily quota or full history.

Changed implementation: quant_data/operations/equibles_transcript_backfill.py,
quant_data/operations/equibles_parallel_backfill.py. Regression coverage:
tests/operations/test_equibles_empty_quarters.py. No commit or push was made.
Evidence is under data/.operations/equibles-empty-quarter-fix-20260911/,
including original source/checkpoint/journal baselines, both test logs,
authorization.json, recovery-result.json, started.json and running-postcheck.json.
The dated worker writes result.json on completion; run.log retains its output.


<a id="recorded-news-batches"></a>

## September 12, 2026 UTC (September 11 Toronto) — news batches reduced to ten

The user explicitly requested ten tickers per Alpaca news batch after reviewing
that this raises the current request count from 47 to 235. This later decision
supersedes the earlier 50-ticker batching scope for the active news selection.
The roster remains 2,343 provider-mapped symbols: 234 groups of ten plus one of
three. Each request still has a 50-article page limit and one page; nine global
feed requests remain singleton, giving a current maximum of 244 requests per
normal hourly invocation. There is no retry, pagination or historical backfill.

Selected request starts are paced at least 0.5 seconds apart within the existing
30-minute service envelope and final-feed reservation. This pacing does not
promise account-wide rate-limit coordination. Existing deferred/full-page/gap
reporting remains. The structural 6,600-symbol ceiling now permits 660 ten-symbol
groups; it grants no authority to broaden the selected roster.

At 01:28 UTC, a quiet immutable host read confirmed unchanged selection/mapping
identities and gaps, all 2,343 symbols exactly once, 235 batches and 50-article
wire parameters. Forty focused/adjacent offline tests passed. The existing timer
was enabled/active/waiting, next due September 12 at 02:10 UTC (September 11,
22:10 Toronto). No manual provider request, canonical write, binding-file change
or recurring-unit mutation was made. The first ten-ticker scheduled run has not
yet been observed. This is a readiness check, not provider completion evidence.

Evidence: .local/news-batch10-20260912/validation.json and tests.log. Details:
[NEWS_UNIVERSE_EXPANSION_2026-09-11.md](NEWS_UNIVERSE_EXPANSION_2026-09-11.md).

### 2026-09-11 selected-company best-effort policy approval

The user explicitly approved implementing the proposed failure policy and
starting the remaining six-group backfill. This supersedes the prior zero-retry
restriction for this population: finish untouched work first, then at most two
retries per temporary failed request and 500 retry attempts total, inside the
original 67,539-request / 8 GiB / original 72-hour deadline allocation. All
charges and missing-response byte reserves persist; no successful population
is repeated. Known provider pauses may clear only their own fully drained
observed guard under the existing lock after waiting; credentials, storage and
accounting faults remain stops. This adds no authority over other collectors.

All six groups retain the explicit weekday 20:00 America/New_York authorization.
The updated entrypoint uses the same policy inside its existing 50,000-request /
4 GiB / 12-hour cycle bounds. The one-use finite worker will audit stored data
and activate that timer after first-pass/retry completion with explicit gaps.
No timer activation is established by this decision entry alone. See the
[collector record](SELECTED_COMPANY_COLLECTORS_2026-09-11.md#september-11--approved-best-effort-completion-policy)
and .local/company-collectors-expansion-20260911/best-effort-preflight.json for
the exact saved population, accounting, executable scope and validation.

Observed launch: the independently reviewed finite worker started under PID
330386 at 2026-09-12 02:04:23 UTC; its started marker followed at 02:04:36 UTC.
The exact launch and review receipts are
.local/company-collectors-expansion-20260911/best-effort-launch.json and
best-effort-independent-review.json. Weekday activation remains conditional
on the worker's subsequent stored-data audit; no current timer activation is
claimed by this launch receipt.

## September 12, 2026 UTC - AVA retained-publication recovery and restart

The user explicitly requested restarting the backfill after the saved AVA
publication stop. The normal 00:10 UTC service had stopped at 03:09:34 UTC
(September 11, 23:09:34 Toronto) after a physical company-store lock timeout.
Its four AVA 2022 quarterly responses were HTTP 200, complete and already
settled in the acquisition journal; no pending or uncertain request existed.
The process had consumed 6,801 shared calls and 322,683,107 bytes in seven
invocations, leaving 3,199 calls in the current 10,000-call UTC-day allowance.

At 03:58:54 UTC the existing replay-safe publisher recovered all four retained
AVA responses without a provider request. Their original bytes/hashes and
capture times were preserved. The checkpoint advanced from 15,745 to 15,749
transcripts and cleared only the exact saved publication block. All prior
15,745 captures/page manifests and the migration ledger were preserved; quota
was unchanged. The blocked checkpoint and settled journal remain in the
operation's before files.

The requested separate dated continuation started at 03:59:38 UTC as PID
372199. It is bounded to September 12, at most 3,199 additional shared charges,
four callers, no automatic provider retries, 3,972,284,189 unused bytes and
17 unused invocations. Its hard cutoff is 06:10 UTC (02:10 Toronto), within
the original scheduled run's six-hour allocation. The current 10,000-call
ceiling and lower fresh provider remaining still constrain dispatch.
The existing credential resolver, publisher, locks, empty-quarter handling,
scope of 2,222 mapped subjects / 26 identity gaps and recurring timer are
unchanged. No recurring service/timer was manually started or reconfigured.

At 04:00:16 UTC the immutable running audit verified 15,771 canonical
transcripts, all 15,745 original captures/page manifests preserved, and 26
new complete bundles including the four zero-GET recoveries. Raw bytes/hashes,
bundle completeness and ingestion lineage passed, with zero transcript
foreign-key violations and an unchanged migration ledger. The first 24 new
responses were HTTP 200. AVA had completed; AVAH was in progress, 665 histories
were complete, and the ledger held 6,828 calls / 3,172 remaining. This is
running evidence, not completed quota or full-history completion.

The 69 passing focused checks from the empty-quarter fix were reused after
verifying unchanged tested source hashes; the publisher and daily-controller
hashes also match prior accepted validation. No shared implementation changed,
so tests and the full suite were not repeated. No commit or push was made.

Evidence is retained under data/.operations/equibles-ava-lock-resume-20260912/,
including authorization.json, checkpoint-before.json, journal-before.json,
canonical-before.json, first-retained-publication.json, recovery-result.json,
recovery-postcheck.json, launch.json, started.json and running-postcheck.json.
The original daily/status reports were preserved before launch. The dated
worker writes result.json when it finishes; run.log retains its output.
The one-use worker is .local/equibles_ava_lock_resume_20260912.py.


### September 12, 2026 UTC - approved ADM v5 transcript pilot

The user accepted the proposed one-capture ADM fiscal 2026 Q2 pilot with
"OK pls go ahead": at most one Terra/high extraction and one Sol/high review
through the existing Codex subscription, each capped at 1,200 seconds and
32,768 output tokens under prompt v5. Review follows only a validated,
published extraction. There is no automatic retry or API fallback.
The scope is capture equibles_transcript_94c0ceecdd9e040ee16ba5fb8f73df91,
using the fixed company store and existing immutable reader/publisher/locks.
No Equibles request, migration, recurring-unit change or bulk extraction is
included. The 113-test offline correction and independent review passed.
Preparation and actual results are retained under
.local/transcript-adm-v5-live-20260912/; approval does not assert completion.


## September 12, 2026 UTC — Sharadar direct compatibility comparison

The user requested comparison of every existing Nasdaq-delivered Sharadar pull
with the direct API using the named SHARADAR_DIRECT_API credential, and source
replacement only if formats match. The finite acquire-only comparison made
exactly 11 single-attempt GETs to api.sharadar.com: three public schemas, an AAPL
ticker sample, fundamentals definitions, and one two-row-limit AAPL sample for
each of the six SF1 dimensions within 2025-01-01 through 2025-12-31. All 11
returned HTTP 200; no retry occurred.

The 112 SF1 fields map with one datekey/date rename; all 28 TICKERS and seven
INDICATORS field names match. The response, metadata, scope-name and pagination
contracts differ, and all eight direct data samples fail existing envelope
validation. The conditional source switch was not performed. No canonical
database was opened or written, no applied migration was edited, and no
scheduler action or historical population repeat occurred.

The [comparison and required migration scope](SHARADAR_DIRECT_COMPATIBILITY_2026-09-12.md)
records 147 field mappings, 1,932 successful sampled cell conversions, limits,
and private evidence. This completed comparison adds no further request,
backfill, retry, scheduler or deployment authority.


#### Result of the September 12 ADM v5 pilot

Terra/high completed one subscription call in about 4 minutes 54 seconds,
reporting 24,247 input and 16,133 output tokens (40,380 total). Its retained
draft contains 22 guidance claims, 22 candidates, 11 substantive question
blocks and 12 topics. The v2 courtesy inventory works on the live output.
The publisher rejected candidate biofuels_second_half because its linked EPS
claim omits source t19. Offline reference inspection also found an unrelated
phase-one investment candidate linked to the EPS-scenarios topic.

Source adjudication found remaining EPS/operating-profit conflation, omitted
Q4 flavors seasonality, unsupported flavors-only operating-profit scope, and
omitted global-technology savings. Prompt v5 does not yet establish extraction
accuracy or readiness for bulk processing.

No analyses or reviews were published. The pilot source bytes/hashes and all
17,855 preflight raw-page metadata records were preserved; ongoing independent
fetching added pages. The company migration ledger is unchanged and the six
analysis tables have no foreign-key errors. No model retry or API fallback ran.

An attempted change to use the second Sol review privately on the failed draft
was rejected by automatic approval review before execution: the approved review
was conditional on a validated extraction. Exactly one model request was used;
no second call, private-review process or workaround started. Reviewing this
rejected draft requires renewed explicit approval. Evidence is retained under
.local/transcript-adm-v5-live-20260912/, including final-audit.json,
reference-diagnostic.json, quality-check.json, review-approval-block.json and
completion-receipt.json. Production implementation was unchanged during the run.

### September 12, 2026: approved upgraded transcript pair test

User decision: “OK go implement and run a live test”, accepting the recommended
Sol/xhigh extractor and Astra/xhigh reviewer. Scope: one private paired test
of retained ADM fiscal 2026 Q2 capture
equibles_transcript_94c0ceecdd9e040ee16ba5fb8f73df91, at most two requests,
one per model, 1200 seconds each, 32768 output tokens per call validated
after completion, no automatic retry, and no API fallback. Astra may review
the schema-shaped Sol draft even if semantic validation rejects it.
Authentication uses the existing pinned native Codex CLI ChatGPT subscription.
No Equibles request, recurring-unit change, or canonical publication is part
of this test. Failed/uncertain calls remain reserved.

Implementation and evidence:
[transcript analysis contract](TRANSCRIPT_ANALYSIS_CONTRACT_2026-09-08.md#september-12-2026-solxhigh-and-astraxhigh-private-pair),
.local/transcript-model-upgrade-20260912/; private operational state
data/.operations/transcript-analysis/model-pair-pilots.
This separate authorization does not execute the previously rejected
conditional Sol review of Terra's draft.

The upgraded private-pair implementation passed 76 focused offline tests (66 compatibility/contract checks and 10 new paired-run checks). The live launch was rejected by automatic approval review before execution: explicit trusted user approval is required to send the saved ADM transcript to OpenAI. No upgraded model call or canonical publication occurred; all launch/native-attempt markers were absent. Evidence: .local/transcript-model-upgrade-20260912/live-approval-block.json and completion-receipt.json. The prepared plan remains unexecuted.

The user subsequently replied “yea go ahead” to the explicit ADM transcript
transfer question, authorizing the named OpenAI destination and both calls,
including Astra review of a rejected draft. The first local runner stopped
before model calls because the shared registry advanced to pending Sharadar
migration 0017 while the store retained its verified 0016 prefix.

The private pair now uses ready_for_private_evaluation: every installed
company migration row must equal the corresponding current-registry prefix,
transcript migration 0011 must be present, and table/dataset/identity checks
must pass. The canonical full-ledger guard remains strict. No migration is
applied or changed. Twelve paired-run tests, including an actual predecessor
temporary store and corrupt/missing-prefix cases, passed; a read-only review
found no blocker.

The same approved plan resumed with zero prior model calls. Live evidence is
.local/transcript-model-upgrade-live-20260912/; the original preparation,
approval rejection, and zero-call readiness-stop evidence remain preserved
in .local/transcript-model-upgrade-20260912/.

The resumed Sol call returned a schema-valid, reference-valid draft with 43
claims, 51 coverage candidates, 11 substantive question blocks and 11 topics.
Its 36211 reported output tokens (including 14003 reasoning tokens) exceeded
the unchanged 32768 post-completion validation limit. The output remains
rejected on that limit. Its unchanged Astra review request is 193069 bytes,
within the existing 200000-byte input bound.

The private runner retains this limit failure while permitting the already
approved review of the failed draft; it cannot turn the pair into a passing
result. Hard input/response bounds, model checks, canonical validation and
all request limits remain unchanged. Fourteen paired-run tests passed after
this correction, including over-limit extraction/review rejection and zero-call
replay. The retained Sol response is replayed locally; only the second
approved Astra call is used. No Sol retry or extra call is authorized.

The approved pair is now complete: exactly two model calls were used.
Sol returned 43 claims and all 11 substantive question blocks, passing
structural/reference checks but exceeding the unchanged output-token limit
(36211 versus 32,768, including 14,003 reasoning tokens). Astra completed with a
needs_changes verdict and four findings. Its review was recovered offline
from preserved native events after a CLI 0.144.4 missing-Astra-metadata warning.
The exact warning is now explicitly retained by the transport rather than
classified as a tool; other errors/tools still fail. Effective Astra xhigh
reasoning was not verified. Three review pointers include an invalid wrapper,
so strict review validation also fails.

No canonical publication or production activation occurred. Both model calls
are spent; no further call or retry was made. The immutable final audit
confirmed all 18,780 prior raw pages, exact source, migration ledger and
analysis rows unchanged, with zero foreign-key errors. Eighty-two distinct
focused checks passed across the implementation; final 32 affected checks
passed after warning handling. Results and unresolved work are retained in
.local/transcript-model-upgrade-live-20260912/LIVE_TEST_RESULT.md and
completion-receipt.json. Original blocked-attempt receipts remain unchanged.


### September 12, 2026: Sharadar direct migration applied

The user explicitly requested implementation after the completed delivery-channel
comparison. Direct Sharadar delivery now supplies the future selected-refresh
code through `SHARADAR_DIRECT_API`; native `date` maps to the established canonical
`datekey` identity. Existing Nasdaq-delivered facts and evidence remain intact.

After the reconciled 2,316-unique-case offline gate and fresh independent
verification, exactly company migration `0017_sharadar_direct` was applied.
The bounded application began at 15:47:18 UTC and verification completed at
15:55:27 UTC. All 15 Sharadar tables (1,459,244 rows) matched their before/after
hashes, including 360,359 fundamental observations, versions, heads and
memberships. All 16 prior company migration rows and 276 prior trigger bodies
were preserved; only the reviewed migration and two origin guards were added.

This operation made zero provider requests, zero backfill requests and zero
scheduler changes. The recorded ARQ OUST–PBF baseline gap and existing
incomplete-baseline refresh guard remain. Existing request and duration caps
are unchanged; an attempted metadata-duration expansion was rejected before
execution and was not used.

[Implementation, validation and application record](SHARADAR_DIRECT_MIGRATION_2026-09-12.md).
Private receipt: `.local/sharadar-direct-implementation-20260912/canonical-result.json`.
This entry records that dated application; it is not a live timer-health check.


## Structured ADM transcript pilot — requested 2026-09-12

The user approved the fixed-section structured extraction mock-up and requested
one repeat test for the same ADM transcript. This explicitly authorizes at most
one Sol/xhigh extraction and one Astra/xhigh final-editor review of capture
equibles_transcript_94c0ceecdd9e040ee16ba5fb8f73df91 (source-labelled
2026 Q2), with complete source input, 1,200 seconds per call and 32,768 output
tokens per completion. Reuse the pinned ChatGPT-subscription transport,
credentials, immutable source reader and existing private pilot state root.
No hidden retry, API fallback, Equibles refetch, canonical publication,
migration or recurring-unit action is authorized by this pilot. Earlier
completed or failed pilots retain their original receipts and consumed quotas.

The [structured workflow contract](TRANSCRIPT_ANALYSIS_CONTRACT_2026-09-08.md#current-reader-workflow--structured-call-records-2026-09-12)
owns the schema and validation rules. Private preflight, model attempt,
response, result and audit evidence belongs under
.local/transcript-structured-live-20260912/. This request record alone is not
evidence that the new model run completed.

Launch status: automatic approval review blocked the structured ADM rerun before either model call started, including after earlier transfer approval was checked. Implementation and 86 offline checks are complete; live calls remain zero. The block requests explicit confirmation for this repeated transcript transfer.


The user subsequently explicitly approved the full-transcript transfer. The two
structured ADM calls completed on 2026-09-12 at 16:46:59 UTC: Sol/xhigh extraction
and Astra/xhigh review, with no retries or API fallback. Sol returned a 582-word
record; Astra revised it to 521 words. Both pass schema, numeric, source-reference
and length checks and stay below the completion-token bound. Native Astra
fallback metadata still prevents verification of its effective reasoning effort;
the automated pair therefore retains needs_changes status.

A subsequent primary-assistant source check restored the capex-above-range caveat,
preserved conflicting Q4 coverage wording, and made the Q4 flavors seasonal low
explicit. These three corrections are separate derivative artifacts, not changes
to either original model response. The checked display is 544 words.
This example does not establish unattended extraction accuracy.

The immutable post-run audit verified the source, source-page metadata, migration
ledger and canonical analysis rows unchanged, with no foreign-key errors. Cached
replay used zero credentials and zero model calls. The finite two-call allowance
is consumed. See .local/transcript-structured-live-20260912/completion-receipt.json
and final-audit.json; the source-corrections.json file records the display edits.


## Explicit Terra/high and Sol/medium ADM comparison - 2026-09-12

The user requested: "OK now can we dial down the model, ie use terra high for
extraction, and sol medium to validate for this example?" This supplies one
new finite two-call comparison for the same saved ADM source-labelled 2026 Q2
capture equibles_transcript_94c0ceecdd9e040ee16ba5fb8f73df91. Prior explicit
approval of the full transcript and resulting draft transfer to OpenAI through
the existing Codex ChatGPT subscription continues to cover that destination.

At most one gpt-5.6-terra/high extraction and one gpt-5.6-sol/medium final-editor
review are allowed, with the same complete source, prompts, structured schemas,
word budget, 1,200-second per-call deadline and 32,768-token completion bound.
The review may use the one allotted call to correct a structurally readable
draft that fails semantic checks. No hidden retry, API fallback, new Equibles
fetch, canonical publication, migration or recurring-unit change is included.

The explicit private selector is structured_terra_sol, with configuration
transcript_structured_call_terra_sol_prompt.v1. The existing structured default
remains Sol/xhigh then Astra/xhigh; frozen historical configurations remain
unchanged. The transport binds the comparison to its fixed model, effort, role,
prompt and schema and retains codex_exec.v2 evidence for both stages.

Private code snapshots, offline checks, source, plan, authorization, native
receipts, model output and immutable audit evidence belong under
.local/transcript-terra-sol-live-20260912/. This dated request entry records
scope and is not evidence that the new model calls have completed.


### September 12, 2026: authorized Sharadar direct operational completion

The user approved the proposed next steps: recover the single held ARQ
OUST–PBF baseline partition, run one capped direct incremental refresh, and
activate the existing daily refresh schedule after successful checks. This
supersedes the earlier held-recovery and uninstalled-schedule status for this
explicit scope; original Nasdaq attempt 362 and the 533 completed partitions
remain preserved. No completed history population is repeated.

The finite gap manifest names exactly 25 existing mapped provider tickers,
ARQ only, date 1900-01-01 through 2026-09-12: at most two GETs (descriptions
and one fundamentals page), 32 MiB, 60 seconds and no retry. The separate
manual incremental invocation retains the existing 200-GET, 256-MiB,
3,600-second bound, 1,000-request daily ceiling and one-second minimum spacing.
Its selected membership remains 2,248, with 2,245 resolved members and 2,223
provider subjects; PFBC/PSQL/TOWN remain mapping gaps. The initial update
window is the completed baseline date minus seven days through today.
Both use SHARADAR_DIRECT_API, api.sharadar.com and the existing fixed company
store, native evidence, publishers and physical-store locks. Descriptions
retain their separate 30-second cap.

The existing nonpersistent timer is authorized at 05:00 America/New_York daily,
with Restart=no and no catch-up or manual recurring-service start. Activation
requires the gap audit, a successful complete direct refresh and its audit.
The fresh preflight found both Sharadar units not installed. The baseline
guard now also recognizes the exact complete direct history scope, including
empty responses; narrow dates, stale upper bounds and incremental-only scopes
do not establish the baseline. No migration, registry or shared publisher
semantics changed in this operational completion.

Evidence is retained under .local/sharadar-direct-activation-20260912/; dated
completion and activation observations are recorded separately below.


The Terra/high and Sol/medium comparison completed at 2026-09-12T17:24:39Z,
using exactly two calls and no retries. Native model time totaled 102.013
seconds (Terra 44.555; Sol 57.458). The 520-word draft became a 581-word final
record. All automated checks passed, with no native metadata warning recorded.

The primary-assistant source check nevertheless found missing capex flexibility,
an unflagged Q4 coverage conflict, omitted flavors seasonality/incomplete Decatur
recovery, and omitted explicit half-year/quarter operating-profit cadence.
The retained unedited model result remains automated validated_private, while
the separate source-quality assessment is needs_correction. No default profile
promotion is implied. Exactly this finite two-call allowance is consumed.

Ninety-two focused offline tests passed in 40.443 seconds. The immutable audit
confirmed the scoped source, source-page evidence, migration ledger and canonical
analysis rows unchanged, with zero foreign-key errors. Replay used no credentials
and zero model calls. No canonical write, provider refetch, migration or recurring
unit action occurred. Evidence:
.local/transcript-terra-sol-live-20260912/completion-receipt.json,
COMPARISON_RESULT.md, source-quality-check.json and final-audit.json.


#### September 12 operational attempt and remaining cap decision

The gap recovery completed at 17:22:15 UTC in 18 seconds: two GETs,
3,237,483 received bytes, one complete direct ARQ capture and 1,497 new
observations. The 17:23:14 UTC audit confirmed all required baseline scopes,
361,856 observations/versions/heads, unchanged migration ledger, zero
Sharadar foreign-key errors and no missing heads. All 533 prior captures and
the original failed Nasdaq attempt remain retained.

The one incremental invocation completed at 17:23:37 UTC with
page_http_failure after two GETs and 42,963 bytes. Descriptions succeeded;
the first fundamentals request returned HTTP 400 with the provider's explicit
200-character ticker-parameter limit. No fundamentals partition or refresh
watermark advanced. No automatic retry or scheduler action occurred.
The subsequent structural store audit passed; it does not establish refresh
completion or permission to activate the timer.

The direct request builder now packs batches within 200 characters as well
as the existing 100-ticker ceiling. The exact selected manifest requires
300 partitions, reusing the successful descriptions response and fixed
2026-09-02 through 2026-09-12 update window. Thirty-seven focused offline
tests passed, plus five isolated activation-gate checks. The gate requires
a successful process/result, no pending checkpoint, matching complete date
and window, and a later passing store audit.

The existing 200-request per-run ceiling remains unchanged. The unapplied
proposal requests a 400-GET manual continuation and future daily cap, with
256 MiB, 3,600 seconds, 1,000 requests/day and no automatic retry unchanged.
This additional request authority is pending an explicit user decision.
Both Sharadar units remain uninstalled/inactive; the first successful full
refresh and its audit still gate their already-authorized activation.

Exact manifests, original failed response, preserved attempt charges, tests,
audits and the unapplied cap patch are under
.local/sharadar-direct-activation-20260912/.


#### September 12: approved 400-request direct refresh limit

The user explicitly replied "approve" to raising the manual continuation and
future daily Sharadar refresh cap from 200 to 400 requests. The prepared
one-line cap change is applied. The 256-MiB, 3,600-second, 1,000-request/day,
one-second spacing and no-automatic-retry limits are unchanged.
One guarded continuation covers the same pending 2026-09-12 window, updating
2026-09-02 through 2026-09-12 across the exact corrected 300-partition manifest.
It reuses successful descriptions and preserves the original HTTP 400/charges.
Activation of the existing daily 05:00 America/New_York timer remains authorized
after successful completion and audit; no service start is authorized by the
activation operation itself. Evidence follows under
.local/sharadar-direct-activation-20260912/continuation-1/.


#### September 12: 400-request continuation stopped; 30-ticker boundary

After the explicit 400-request approval, 24 focused checks passed. The single
new continuation at 19:46:45 UTC made one GET, received 173 bytes and stopped
with HTTP 400: the provider also accepts at most 30 tickers per request; the
first character-limited batch contained 44. It published no new fundamentals
and left all 450 corrected partitions pending. Cumulative live requests in this
operational completion are five: two successful gap GETs, the initial two-GET
refresh attempt, and this one failed continuation GET. No automatic retry or
scheduler operation occurred.

The builder and parser now enforce both 30 tickers and 200 characters. The fixed
selected update manifest therefore contains 450 partitions, 75 per dimension,
with maximum 143 ticker characters. Its existing successful metadata is reused.
The pending-checkpoint bound now accommodates all 450 partitions; an isolated
restart test verifies that a fully acquired 450-partition checkpoint finalizes
without another GET. The fixed selection, dates, byte/time ceilings, stored
history and all previous attempt charges are unchanged.

Production remains at the approved 400-request cap. The separate unapplied
500-request proposal covers one corrected continuation and future daily runs
(the full daily manifest requires 450 fundamentals GETs plus metadata).
It retains 256 MiB, 3,600 seconds, 1,000 requests/day and no automatic retry.
New request authority is pending; the timer remains uninstalled/inactive.
Private evidence: continuation-1/,30-ticker-refresh-manifest.json,
30-ticker-final-tests.log,500-request-proposal.json and
proposed-500-request-cap.patch under
.local/sharadar-direct-activation-20260912/.

The final correction passed 40 unique focused tests (39-case check plus a
27-case affected recheck including the added checkpoint case). Independent
read-only review reconciled the tests and all five source hashes, verified
the exact 450-partition manifest, and found no remaining code blocker.
Operational continuation and timer activation remain incomplete.


## Ten-ticker saved-history extraction with sampled review - 2026-09-12

The user requested full-history Terra extraction for ten tickers using the
current approach, followed by a few random Sol quality checks and an aggregate
report. The user selected the first ten eligible saved tickers: A, AA, AAL,
AAMI, AAOI, AAON, AAP, AAPG, AAPL and AAT. Read-only inventory found 234
distinct saved source-labelled quarters across those tickers. Full history here
means all saved quarters at preparation, retaining source fiscal labels and
choosing the latest capture if a quarter has multiple captures.

This current decision authorizes at most one Terra/high extraction per saved
quarter (234 maximum) and five Sol/medium reviews sampled uniformly without
replacement from structurally readable drafts. Full saved source text, and the
resulting draft for reviews, go to OpenAI through the established Codex ChatGPT
subscription. The schema, prompts, 1,200-second call deadline and 32,768-token
post-completion output check are unchanged. Three concurrent model requests
are permitted within this finite workload. No retry, API fallback, Equibles
refetch, canonical publication, migration or provider scheduling action is added.

The batch uses transcript_structured_call_terra_sol_prompt.v1 and the existing
private replay-safe exchange receipts and native attempt markers. It freezes
source bytes, exact request hashes, the random seed, sampled capture ids and
the complete finite manifest. Item failures are retained and other quarters
continue; three consecutive transport failures stop new queued requests.
The batch cannot silently replace failed reviews or re-extract completed items.
Sample approvals and edits are aggregate review outcomes, not a measured
population accuracy percentage. Unreadable/failed extractions remain separately
counted and do not disappear from the completion denominator.

Implementation: quant_data/operations/transcript_history_batch.py.
The durable state root is data/.operations/transcript-analysis/history-batches;
existing model-pair-pilots and codex roots retain native request evidence.
Private launch/test/audit evidence: .local/transcript-history-10-20260912/.
This records the requested finite scope, not completion of live model work.


Ten-ticker batch readiness: 62 focused offline tests passed in 32.388 seconds.
The complete 234-source preflight passed, with no rejected input. Frozen plan:
87869db0d215f1df0fef2318660ec1e09219b01149bccf7f8abd3b856a4008d1.
Automatic approval review rejected the live launch before execution because
the trusted user messages did not explicitly approve exporting this batch's
full transcript payload and five sampled drafts to OpenAI. No workaround or
indirect launch was attempted. Launcher, start, model-attempt and result
markers and the batch run directory are absent; new model calls remain zero.
The exact prepared finite batch is retained awaiting that transfer approval.
Private evidence: .local/transcript-history-10-20260912/launch-block.json and
completion-receipt.json. No completion monitor was created for an unstarted run.


#### September 12: approved 500-request direct refresh limit

The user explicitly approved the proposed 500-call limit for one corrected
continuation and future daily updates. The applied per-run cap is now 500
requests; 256 MiB, 3,600 seconds, 1,000 requests/day, one-second spacing and
no automatic retry remain unchanged. The fixed continuation contains 450
fundamentals partitions for the existing selected universe, all six dimensions
and updates from 2026-09-02 through 2026-09-12, reusing retained descriptions.
Both request boundaries are enforced: at most 30 tickers and 200 characters.
The prepared continuation-2 runner retains all previous attempts and checks
its separate approval receipt. Daily 05:00 America/New_York timer activation
remains conditional on a successful completed refresh and subsequent audit.
Evidence: .local/sharadar-direct-activation-20260912/500-request-approval.json
and continuation-2/.


The user subsequently explicitly replied "yes approved" to the exact full
234-transcript OpenAI transfer and five sampled transcript/draft review question.
The prepared batch launched at 2026-09-12T22:57:48Z as Linux PID 541576, with
native model-attempt evidence confirmed. The rejected-launch completion receipt
is preserved separately as launch-block-completion-receipt.json. The same
239-call maximum, three concurrent calls, timeout and no-retry rules apply.
This is running-state evidence, not a completed quality result.

A proposed recurring completion heartbeat was rejected by automatic approval
review because that persistent automation was not explicitly requested. No
heartbeat was created and no workaround automation was installed; the active
task follows the running finite batch directly.


#### September 12: Sharadar direct refresh complete and daily timer activated

At 23:06:15 UTC the approved continuation completed all 450 exact selected
partitions in 450 GETs, receiving 7,507,821 bytes in 456.231 seconds, exit 0
and zero automatic retries. The fixed September 12 checkpoint completed.
The 23:07:25 UTC immutable audit verified 1,321 new observations, 236 changed
versions and 1,940 unchanged-version reuses across 3,497 capture memberships.
Canonical totals are 363,177 observations/heads and 363,413 versions. All
prior migration rows and original request/response hashes were preserved;
foreign-key, lineage, semantic-duplicate and missing-head checks returned zero.
The earlier ARQ gap recovery added 1,497 observations. All named baseline
scopes are now complete; PFBC, PSQL and TOWN retain their three identity gaps.

At 23:07:52 UTC the reviewed service and timer were installed in the existing
user manager, and only quant-data-sharadar-selected-refresh.timer was enabled
and started. It was loaded/enabled/active/waiting, with next trigger
September 13, 2026 at 05:00 EDT. Its daily 05:00 America/New_York cadence,
Persistent=false and Restart=no remain unchanged. The service was loaded
and inactive with empty execution timestamps; activation made no provider
request or manual recurring-service start. The effective per-run allowance is
500 requests, 256 MiB and 3,600 seconds, with the 1,000-request daily ceiling
and one-second spacing. Source and installed unit bytes match.

The final cap change passed 27 affected offline checks; the batching and
checkpoint correction had already passed 40 unique focused checks. Independent
read-only verification matched all 450 audited scopes to the manifest,
reconciled counts and hashes, and certified the saved activation state.
Future scheduled execution has not yet been observed. All five requests
from earlier stopped attempts/gap recovery remain accounted for: 455 total
provider requests in this operational completion, without automatic retries.
Evidence: .local/sharadar-direct-activation-20260912/continuation-2/, including
result.json, refresh-audit.json, ready-for-timer.json, timer-activation.json,
completion-receipt.json and independent-verification.json.


The ten-ticker batch completed at 2026-09-12T23:52:57Z, approximately 55 minutes
after launch. All 234 saved quarters produced structured Terra/high output;
83 passed every automated check and 151 were flagged, predominantly for
source-reference and repeated-content rules. Five randomly sampled Sol/medium
reviews completed: zero unchanged approvals, four revised records passing
the review checks, and one record still needing attention. These are validation
and sampled review outcomes, not a population factual accuracy measurement.

Exactly 239 model calls were consumed, with no retry or additional call during
the reporting follow-up. All 239 retained response hashes and the frozen random
selection were verified locally. The immutable completion audit confirmed source
text, selected pages, migration ledger and canonical analysis rows unchanged.
No canonical publication occurred. The workload is complete; no completion
heartbeat is required or was created. Aggregate evidence is retained in
.local/transcript-history-10-20260912/aggregate-quality.json and
AGGREGATE_QUALITY.md, alongside the original completion receipt and audit.


## Tiered structured-call quality standard and fresh five-review sample - 2026-09-13 UTC

The user accepted the annotated standard: substantive errors block acceptance;
missing speaker labels or conflicting source statements are uncertainty flags;
repetition and modest length overruns are editorial warnings. The user requested
implementation and another random sample of five reviews of the retained
structured extractions. This supplies exactly five new Sol/medium calls.
The already explicitly approved full saved transcript and draft transfer to
OpenAI through the Codex ChatGPT subscription is reused for the same retained
population. No Terra extraction, provider refetch, retry, canonical publication,
migration or recurring automation is part of this operation.

The new private profile is transcript_structured_tiered_prompt.v2, selected as
structured_tiered by the paired runner. It keeps the existing structured brief
schema and introduces transcript.structured_quality_review.v2 with classified
draft findings, separate source uncertainties, and unresolved substantive issues.
Legacy profiles, source role mapping, canonical schemas and historical evidence
remain unchanged. The review-only runner uses the existing private exchange
receipts, binds each review to its original Terra response hash, and excludes
the five previously sampled records before freezing a fresh random sample.

The local assessment collects findings rather than stopping at the first one.
Incorrect identity, impossible/contradictory numerical values, incompatible
guidance, nonexistent references and known analyst/operator statements attributed
to management remain blocking. Unknown speaker roles remain unknown and flag
attribution uncertainty; they are never silently assigned to management.
Repeated comparison labels across distinct metrics are valid. Exact duplicate
metrics with identical values are editorial; conflicting same-metric values
block. Modest cell/document overruns up to 25% are editorial, while greater
overruns and existing schema/request bounds still block. Entire summaries cannot
exceed the source word count. Source-context factual checking remains the model
reviewer's responsibility; mechanical acceptance is not a factual accuracy score.

One Sol review compares each original saved Terra draft with its complete saved
source and the local assessment. Draft errors remain classified as substantive
even when the reviewer corrects them. Source uncertainties do not themselves
force needs_attention; only unresolved substantive/hard-bound problems do.
No historical extraction, review or summary is rewritten by reassessment.

Seventy-seven focused offline checks passed in 35.028 seconds, including 15
tiered-policy/resampling checks and prior profile/transport/history compatibility.
The private frozen plan, reassessment, exact request hashes, native attempt and
completion evidence belong in .local/transcript-tiered-review-20260913/.
This entry records the implementation and finite authorization, not completion
of the five live review calls.


Tiered implementation follow-up: the final 78 focused checks passed in 37.000
seconds. The local reassessment retained 38 records without flags, 165 with
uncertainty/editorial flags, and 31 with substantive or hard-bound blockers
(203/234 mechanically unblocked). This is not a factual accuracy result.
A malformed retained point measure exposed a renderer exception during the
first local preflight. The checker now keeps its numeric blocker and marks
word count unavailable without stopping the population assessment; the original
failure and the added regression check are retained. No model call was used.

The five fresh Sol/medium reviews are frozen as plan
f5736d90f90855d961fc6025835f39f84b4466bd6f6aab000ed5c28e96213ab7,
excluding all five previously sampled records. Automatic approval review
rejected the launch before execution and requires explicit approval for this
additional five-source/five-draft transfer to OpenAI. No workaround or retry
was attempted. Start, attempts, result and audit markers and all new review
reports are absent; new calls remain zero. Evidence:
.local/transcript-tiered-review-20260913/launch-block.json,
completion-receipt.json and IMPLEMENTATION_RESULT.md.


### Tiered five-review completion, 2026-09-13 01:46 UTC

The user explicitly approved the exact additional five full saved sources and
original Terra drafts being sent to OpenAI for Sol/medium review with
"sure go ahead". The transfer approval is retained at
.local/transcript-tiered-review-20260913/explicit-transfer-approval.json.
This supersedes the pending-launch state above for the same frozen plan
f5736d90f90855d961fc6025835f39f84b4466bd6f6aab000ed5c28e96213ab7.
The previous blocked completion receipt was preserved separately as
launch-block-completion-receipt.json; its historical rejection was not erased.

Exactly five review calls completed from 01:40:29 through 01:46:29 UTC.
Three review packages passed; two failed the category/severity consistency
check in reviewer findings. Both failed raw reviews remain intact and are not
promoted to passed status. Separate local checks accepted all five revised
briefs with nonblocking flags. Sol reported substantive draft findings in all
five raw reviews, missing substantive Q&A in four, and source uncertainty in
four. The original live summary counts substantive findings and uncertainty
only where review validation completed; aggregate-quality.json explicitly
distinguishes that subset from raw claims in the two unvalidated packages.
This sample is not a population accuracy estimate.

The final audit confirms all 234 original source/draft pairs unchanged,
zero new extraction calls, zero model calls or credential access on replay,
and no canonical database access or publication. There were no retries.
The finite five-call approval is consumed; it grants no further run, repeat,
retry or scheduled operation. Completion and readable aggregate evidence:
.local/transcript-tiered-review-20260913/completion-receipt.json,
final-audit.json, aggregate-quality.json and REVIEW_RESULT.md.
The tested implementation remains the 78-check baseline recorded above.


### Astra/medium adjudication of the five Sol reviews - 2026-09-13 UTC

The user explicitly requested Astra/medium to assess Sol's review conclusions
against the raw transcripts, the extracted structured information and the goal
of concise, informative structured call summaries, and to issue a final verdict.
The bounded scope is one fresh Astra/medium adjudicator covering the same five
cases from tiered resample plan
f5736d90f90855d961fc6025835f39f84b4466bd6f6aab000ed5c28e96213ab7,
including both Sol packages whose review labels failed validation.

The route is a fresh default-role Codex subagent with explicit gpt-6-astra and
medium overrides, as requested by the user, rather than the preset verifier's
xhigh effort. Its inputs are hash-verified private copies of the five complete
saved source-turn sets, original Terra drafts, and unedited Sol reviews with
revised briefs. The original 234 source/draft pair hashes were also verified.
This uses one bounded delegated adjudication session; it does not create five
new CLI review calls, refetch transcripts, re-extract drafts, change the
production workflow or access/publish to the canonical database.

Only new private verdict artifacts may be written by the adjudicator. No
recursive delegation or automatic repeat is included. Parent-owned evidence:
.local/transcript-astra-adjudication-20260913/authorization.json,
input-manifest.json and started.json. This entry records authorized dispatch,
not completed adjudication.


Astra/medium adjudication completed for all five bundles and all 235 source
turns. The fresh adjudicator classified Sol's 15 findings as 1 substantively
justified, 13 valid but nonblocking, and 1 unsupported/overstated. Only 1 of
Sol's 10 labeled blockers warrants blocking, on a narrower core-theme rationale.
Astra judged the original Terra briefs: 1 usable as is, 2 usable with caveats,
2 needing targeted corrections. Sol revisions: 1 usable as is, 3 with caveats,
1 needing a targeted correction. These are independent sample judgments, not
population accuracy or an alteration of the prior saved validation statuses.

Astra found that empty analyst-focus fields often coexist with Q&A substance
already included elsewhere. It also identified a risk-wording issue Sol did
not list, one material qualification omitted by a Sol revision, and three
malformed revised topic titles. Its verdict favors useful existing drafts and
a compact correction list; it does not justify full re-extraction or a workflow
rewrite. No recommended correction or production policy change was applied
within this adjudication-only request.

Parent checks reconciled every finding index and aggregate count, confirmed all
cited source-turn IDs exist, and verified the 15 protected original files and
all five frozen bundles unchanged. The two verdict files have mode 600.
One requested Astra/medium delegated session was used; no extractions, Codex
CLI calls, provider fetches or canonical database operations were performed.
Evidence: .local/transcript-astra-adjudication-20260913/ASTRA_VERDICT.md,
astra-verdict.json, final-audit.json and completion-receipt.json.


## Structured transcript database storage - authorized September 13, 2026 UTC

The user requested that the saved structured extractions be stored in the
database with source links, model/version information and review status.
This authorizes the finite import of the existing 234 original Terra/high
outputs, their 234 retained automatic assessments, 10 Sol/medium reviews and
5 Astra/medium per-case adjudications into data/company.sqlite. No model call,
provider request, re-extraction, source rewrite, recurring-unit action or public
exposure is included. Storing a draft does not mark it factually accepted.

Allocate company ordinal 18, company:0018_structured_transcripts, after the
verified company 0017 ledger. The new private derived dataset
company.transcript.structured owns company_structured_transcript_outputs and
company_structured_transcript_assessments. It has no collector, tool, dashboard
or export binding. Original structured output and raw model evidence are
immutable. Separate assessments retain mechanical status, failed Sol package
status, original Sol revisions, and Astra disagreement without overwriting the
original Terra draft. Model availability timestamps remain distinct from the
database publication timestamp; as-of reads exclude later assessments.

The active registry stays at 2.82 during development. The candidate registry
2.83, forward migration checksum and exact predecessor proof are pinned in
.local/transcript-db-publication-20260913/allocation.json. The prepared import
is a bounded hash-addressed manifest with per-record files and no database or
network access during loading. All 234 retained source/draft identities and
all reviewer request/response bindings were checked. Publication must pass the
full offline suite on the isolated candidate baseline and fresh independent
verification before activating the registry and applying the migration.
Source lineage, immutable rows, rollback, cutoff and exact zero-write replay
are covered by ten passing focused temporary-store checks. Allocation and
private artifacts are not proof of canonical application; completion evidence
will be appended after the authorized operation.


### Structured transcript storage completion - September 13, 2026 UTC

The authorized existing-batch import completed at 2026-09-13T05:03:17.263570Z: 234 original
Terra/high structured outputs and 249 separate assessments (234 automatic,
10 Sol reviews, 5 Astra adjudications) are stored in data/company.sqlite.
Storing drafts preserves their original quality status; it does not assert
acceptance or replace them with reviewer revisions. No extraction/review pipeline
model calls, provider requests, recurring-unit changes, source rewrites or public
exposure were performed.

Company migration company:0018_structured_transcripts was applied with
SHA-256 22d587532a4f5ecffbc76e2e16b793b312fc9f116815474328ec4e2ba05d9111.
Actual migration registry provenance: 2.83.0.
All prior company migration rows and existing SQL definitions were preserved.
All 234 canonical source records and original extraction bytes, plus all 249
assessment payloads and evidence bytes, matched the prepared batch after import.
The new tables passed foreign-key checks. Exact replay wrote zero records,
created no new ingestion run, and left the dataset's stored state unchanged.

The complete reconciled offline gate covered 2381 unique cases
with 0 skips and zero unresolved failures/errors. Required
correction cases passed actual reruns. Fresh independent review cleared the
implementation, frozen-runtime harness and evidence reconciliation methods.
No full-suite certification of unrelated shared-runtime changes is claimed.

During validation, unrelated work advanced the shared active registry beyond
the initial 2.82 development baseline. The company declarations remained exactly
compatible. This import used the hash-verified frozen 2.83 runtime and preserved
the active registry, which was 2.84.0 at final verification.
This supersedes only the earlier prospective registry-activation sequence;
the authorized population, storage semantics and gates did not broaden.

Evidence: .local/transcript-db-publication-20260913/completion-receipt.json,
canonical-schema-audit.json, canonical-final-audit.json, full-suite-result.json,
frozen-runtime-verification.json and DB_STORAGE_RESULT.md. Future extraction
auto-publication was not enabled by this existing-batch import.


## Full saved-universe Terra extraction and incremental storage - September 13, 2026 UTC

The user requested: "OK great, now pls proceed with the extractor for the full
universe, and store the extracted outputs in our db". This supersedes the
prior ten-ticker extraction boundary for unprocessed saved quarters. The
completed 234-quarter population remains excluded from repeat extraction.

The frozen inventory at 2026-09-13T14:45:28.377236Z contains 28,030 saved
source-labelled quarters across 1,231 tickers. Of these, 234 are already in the
structured dataset. The finite requested remainder is 27,796 quarters across
1,221 tickers, selecting the latest saved capture per source-labelled quarter.
Source-preflight failures remain in the denominator and consume no model call.
Newly fetched transcripts after this snapshot do not extend this batch.

The established Terra/high structured profile and Codex ChatGPT subscription
transport are reused: at most one request per ready quarter, up to 27,796
extraction calls, three concurrent requests, 1,200-second deadline, zero
automatic retries, zero new Sol/Astra transcript-review calls, zero Equibles
requests and no API billing fallback. Full saved transcript text goes to OpenAI
through that existing extraction transport. No recurring unit is added or
modified; this is a finite checkpointed process.

Implementation: quant_data/operations/transcript_universe_batch.py. Each
schema-shaped original output and its tiered automatic assessment are published
through StructuredTranscriptPublisher into company.transcript.structured.
Quality flags and blocked drafts remain explicit. Malformed responses are
retained privately and other quarters continue. Three consecutive transport
failures pause queued work; source-integrity or publication failures also pause
new requests. Already attempted failed model requests are not retried on resume.
Unattempted entries remain within the same fixed cap. Publication recovery
reuses verified saved response bytes, creates no new model request, and uses
the existing zero-change replay semantics after a prior commit.

The runner stores hashed plan pages and full source snapshots under
data/.operations/transcript-analysis/universe-batches, reusing the existing
model-pair-pilots and codex evidence roots. It takes short existing company
locks for immutable source reads and publication, with network work outside
database locks. No schema, migration, shared registry, scheduler, store path,
credential mechanism, or public tool/export changes are included.

Thirty-one distinct focused checks passed: eleven new temporary-store runner
checks plus ten existing history-batch and ten existing structured-storage
checks. The initial new-test fixture helper collision was corrected and all
eleven new tests reran successfully. Tests used synthetic responses and explicit
temporary stores. Private evidence: .local/transcript-universe-20260913/.
This is preparation and finite-scope evidence; actual start and outcome are
recorded separately.


Full-universe preparation completed at 2026-09-13T15:04:06.511867Z.
All 27,796 selected inputs passed; frozen plan
9836d71ad94b77166d320a3010bf447d29dfa796c82d3031624f6009993361d8
has an exact 27,796-call maximum and zero review calls. Automatic approval
review rejected the live launch before process creation because the current
user request did not explicitly authorize sending this expanded full raw
transcript payload to OpenAI. No workaround or indirect launch was attempted.
The prepared workload remains unchanged awaiting that explicit transfer approval.

Verified after rejection: zero request receipts for this plan; no start,
authorization, run-log or run-directory marker; all 234 previously stored
structured-output identities unchanged and zero newly stored outputs.
Evidence: .local/transcript-universe-20260913/launch-block.json and RUN_STATUS.md.
This is a blocked-before-launch result, not extraction completion.


### Full-universe transfer approval and launch - September 13, 2026 UTC

The user explicitly replied "approve" to sending the full 27,796 prepared
transcripts to OpenAI through the existing subscription, using at most 27,796
Terra/high calls and saving the outputs in the company database.
This resolves the prior automatic-approval launch block for the exact plan
9836d71ad94b77166d320a3010bf447d29dfa796c82d3031624f6009993361d8.
The original launch-block evidence remains preserved.

The finite background process launched at 2026-09-13T15:20:47.971472Z as Linux PID
998468. Three native extraction request receipts were confirmed
pending at startup, within the approved concurrency. This is evidence of a
running batch; it does not claim completed extraction or publication.
The unchanged cap, no-retry policy, failed-item continuation and incremental
database publisher apply. No recurring automation or provider refetch was added.

Approval and actual start evidence:
.local/transcript-universe-20260913/explicit-transfer-approval.json,
authorization.json, started.json and run.log.


Initial live publication verified at 2026-09-13T15:24:17.627244Z: the first nine new
structured outputs and nine automatic assessments were read back and matched
their original model bytes, structured JSON, canonical source linkage and
assessment evidence. New-table foreign-key checks passed; all 234 previous
structured outputs retained their semantic identities. The running progress
snapshot showed 13 stored outputs and
27783 remaining. This is initial-run validation, not full
population completion. Evidence:
.local/transcript-universe-20260913/initial-publication-audit.json.


## September 13, 2026 UTC - ITGR lock recovery and daily quota completed

The user explicitly requested resuming the stopped backfill and using today's
full API quota. This authorized a separate dated continuation of at most 267
additional shared calls after the scheduled run stopped at 9,733 charges.
The continuation retained four callers, zero provider retries, the shared
10,000-call UTC-day ceiling, at most 30 minutes, 3,881,310,098 unused bytes and
14 unused invocations. It reused the existing named EQUIBLES_API_KEY resolver,
fixed 2,222 mapped subjects / 26 identity gaps and company.sqlite publisher.
No recurring service or timer was started, reset, or reconfigured.

The scheduled run's 04:57:51 UTC stop was a physical company-store lock
timeout on ITGR 2020 Q1. All four ITGR 2020 quarterly responses were HTTP 200,
complete, retained and settled, with no pending or uncertain request. At
15:30:33 UTC the existing replay-safe publisher and cache-only runner had
recovered all four without a GET or quota change. The checkpoint advanced
from 28,030 to 28,034 transcripts. Original captures, page manifests, response
bytes/hashes, capture times and the migration ledger were preserved.

The dated continuation finished at 15:38:26 UTC with outcome daily_cap:
267 new calls, 11,630,919 received bytes, one invocation and no provider
retry. Both the shared ledger and fresh provider headers confirmed 10,000
used / zero remaining. It added 247 complete transcripts after recovery,
bringing the canonical total to 28,281. There are 1,330 completed histories
and 892 remaining mapped subjects; JOBY is current. No saved block or pending
attempt remains. The enabled timer's next normal run was observed at
September 14 00:10 UTC (September 13 20:10 Toronto).

At 15:38:40 UTC the locked immutable completion audit preserved all 28,030
prior capture records and page manifests, validated all 251 new complete
bundles including the four zero-GET recoveries, checked raw hashes, retained
bytes and ingestion lineage, and found zero transcript foreign-key violations.
The migration ledger and source/binding/registry/unit hashes were unchanged.

All 69 focused publisher, collector, parallel, daily-controller and
empty-quarter tests passed in 16.261 seconds, with no skips. The one-use
preflight initially named a nonexistent page_offset column; it stopped before
checkpoint or canonical mutation and passed after using the existing
turn_offset column. No shared implementation or schema changed, so the full
suite was not repeated. No commit or push was made.

Evidence: data/.operations/equibles-itgr-quota-resume-20260913/ contains
authorization.json, preserved checkpoint/status/journal/canonical baselines,
validation-receipt.json, recovery-result.json, recovery-postcheck.json,
started.json, result.json and completion-postcheck.json. The one-use helper
is .local/equibles_itgr_quota_resume_20260913.py.


## Luna/high extraction and five Astra/high reviews - September 13, 2026 UTC

The user requested stopping the Terra universe runner, storing all completed
outputs, switching extraction to Luna/high, and reviewing Luna against full raw
transcripts and the project's concise structured-call goal with Astra/high.
The user then selected five random unprocessed transcripts, at most five Luna
extractions plus five Astra reviews (10 calls total), no automatic retries,
and no full-universe Luna launch in this comparison.

Terra PID 998468 received a parent-only SIGINT at 15:48:34 UTC. Its three
in-flight workers finished; it exited with 122 completed outputs, all verified
against exact saved model bytes, JSON, source linkage and automatic assessments
in company.sqlite at 15:52:27 UTC. Total prior structured outputs: 356.
No completed output remains unpublished. Both structured tables passed foreign
key checks; the retained run status is stopped_by_user.

The new transcript_structured_call_luna_astra_prompt.v1 profile uses
gpt-5.6-luna/high for extraction, preserving the previous extraction instructions
and structured schema. New universe plans select Luna; frozen Terra plans retain
their exact model, prompt and request identity. Astra/high uses the tiered review
contract and explicitly assesses the original draft's usefulness, concision and
factual fidelity. Corrections are separate candidates and do not replace the
original extraction. The storage allowlist admits the exact new Luna profile;
no migration, shared schema, scheduler or credential mechanism changes.

The random sample is ESI 2024 Q1, ACAD 2021 Q1, LOW 2020 Q2, C 2022 Q3 and
AYI 2025 Q4, drawn from 27,673 unprocessed/unattempted ready entries in the
original frozen universe. Full saved sources and then source/draft pairs go to
OpenAI through the existing subscription transport. Each request has a
1,200-second deadline, three concurrent calls maximum and no retries.
Original Luna drafts and automatic assessments publish through the existing
replay-safe company publisher. Astra review evidence stays private for reporting.

53 focused offline checks passed with zero failures, errors or skips, covering
new profile/role enforcement, unchanged extraction inputs, exact Luna storage,
replay/no-repeat behavior and prior Terra/transport/storage compatibility.
Evidence and pinned preparation:
.local/transcript-luna-astra-20260913/. This records preparation and authority;
actual live outcomes are recorded after execution.


The five-source Luna/Astra live launch was blocked before process creation by
automatic approval review. The initial local permissions preflight was corrected
before any model request. A subsequent read-only proof confirmed all five full
sources exactly match the prior explicitly approved OpenAI-transfer universe;
automatic approval review still rejected the identical launch and requires a
fresh explicit payload/destination approval. No alternate transport, indirect
launch, repeat model call or workaround was attempted.

At the blocked handoff, both new plan IDs have zero request receipts, the pilot
start marker is absent, and no Luna/Astra quality result exists. Terra remains
stopped with all 122 completed results verified in the database (356 total).
Implementation and 53 focused checks are complete. Preserved evidence:
.local/transcript-luna-astra-20260913/blocked-result.json,
launch-block.json, existing-transfer-approval-proof.json,
launch-reconsideration-block.json and COMPARISON_STATUS.md.


### Explicit Luna/Astra transfer approval and start - September 13, 2026 UTC

The user replied "approve" to the exact five-full-transcript and generated-draft
transfer to OpenAI through the existing Codex subscription for five Luna/high
extractions and five Astra/high reviews. This resolves the prior launch block
for pilot 9727c687d3c7aae0168a0fd935f24b75a4f0303d4818e46dcc5529a95f15cb17; all rejection evidence remains preserved.
The unchanged pilot started at 2026-09-13T16:06:16.984873Z. The fixed maximum is 10 calls,
zero retries and no full-universe Luna run. This records start, not completion.
Evidence: .local/transcript-luna-astra-20260913/explicit-transfer-approval.json
and started.json.


### Luna/Astra sample completed with review limitations — September 13, 2026 UTC

The fixed pilot finished at 2026-09-13T16:11:44.775914Z: exactly five Luna/high extractions
and five Astra review calls, zero retries. All five original Luna outputs and
automatic assessments are verified in company.sqlite, 361 total. All 356 prior
outputs are preserved, and both structured tables passed foreign-key checks.
Every review is bound to its full frozen source and original Luna response.

Summaries contain 349–529 words versus 4390–8953
source words, a combined 6.3% length ratio. All five automatic results
are accepted_with_flags for source_metadata only; this is not factual approval.
All five raw reviews recommend targeted coverage/qualification/metric corrections.
Three review packages pass consistency checks; ACAD and C fail category/severity
consistency. Their raw judgments are retained, not promoted to validated reviews.

All five native Astra calls requested high but emitted fallback model metadata.
The CLI catalog lacks Astra, so effective reasoning effort is unverified.
This is not a verified Astra/high quality pass. No extra review, retry,
replacement extraction or full-universe Luna run was made. The ten-call approval
is consumed. Evidence: .local/transcript-luna-astra-20260913/QUALITY_REPORT.md,
aggregate-quality.json, completion-receipt.json, result.json, reviews/ and universe/.


## Astra/xhigh personal-project review of original Luna outputs - September 13, 2026 UTC

The user explicitly requested the same five Luna outputs be reviewed by
Astra/xhigh against their original transcripts, emphasizing personal-project
quality/value/cost, accurate cited numbers over exact quotation, and a final
verdict on Luna's quality. This authorizes one fresh bounded Astra/xhigh reviewer
session covering ESI, ACAD, LOW, C and AYI. There are no new extractions, native
CLI model calls, provider fetches, canonical-store accesses, retries or automatic
full-universe launch in this review.

The fresh default-role reviewer /root/astra_xhigh_luna_value_review was dispatched
with explicit gpt-6-astra and xhigh settings. It uses the app's delegated-model
route, rather than the legacy CLI whose Astra reasoning setting was unverified.
Inputs are exactly the five prior original Luna drafts and full source snapshots,
hash-verified against original exchange evidence, covering all 259 source turns.
Earlier review opinions and corrected candidates are excluded to avoid anchoring.

The reviewer must distinguish genuine numerical/meaning errors from paraphrasing,
missing speaker metadata, harmless shorthand, optional detail and editorial
preferences, while retaining decision-relevant qualifications and central themes.
It gives a final default-extractor recommendation and the minimum proportionate
quality checks. Only two private verdict artifacts may be written; original
sources, drafts, database contents and production configuration stay untouched.
Preparation/dispatch evidence:
.local/transcript-astra-xhigh-personal-review-20260913/input-manifest.json,
authorization.json and started.json. This entry records dispatch, not completion.


### Completed fresh Astra/xhigh personal-project review

Completed at 2026-09-13T16:37:29.073642Z. The fresh explicitly assigned gpt-6-astra/xhigh reviewer
read all 259 source turns (33,233 words) and the same five original Luna/high
outputs, checking 29 reported-result and 22 guidance entries. Its final verdict
is 1 usable as-is, 4 usable with minor caveats, 0 needing material correction,
and 0 unsuitable. It found no materially wrong financial number or misleading
meaning in the selected results and guidance under the user's concise
personal-research standard.

Astra recommends Luna/high as the default, with focused checks on consequential
figures and qualifications, local citation/wording repair, and stronger-model
review only for difficult or consequential cases. This is a five-case practical
judgment, not a population accuracy, measured cost/weekly quota, or matched
Terra comparison. The optional prompt adjustment was not implemented.

Parent verification passed: all five input bundle hashes and all 20 protected
original evidence hashes were unchanged, complete turn coverage and aggregate
counts reconciled, and 17 review pointers resolved. Both verdict files are
private mode 600. No new extraction, native CLI invocation, provider fetch,
canonical-store access, database publication, configuration change, or
full-universe restart occurred. The latest previously verified database total
remains 361; it was not re-queried for this review. Earlier review records remain
preserved as historical evidence.

Evidence: .local/transcript-astra-xhigh-personal-review-20260913/ASTRA_VERDICT.md,
astra-verdict.json, final-audit.json and completion-receipt.json.


## Resume remaining original transcript universe with Luna/high - September 13, 2026 UTC

The user explicitly requested resuming Luna for the remaining transcripts and
storing the outputs in the database after the successful five-case Astra/xhigh
review. This supersedes the earlier sample-only pause for this bounded remainder.
The prior full-transcript transfer approval remains recorded at
.local/transcript-universe-20260913/explicit-transfer-approval.json.

The locked immutable inventory at 2026-09-13T16:42:08.504075Z verified 361 stored
structured outputs. Of the original 27,796 selected quarters, 127 are now stored;
27,669 remain without a stored output. The resumed batch selects exactly 27,668
unattempted original source captures across 1,217 tickers. ADM 2026 Q2, which has
an earlier model-request receipt, is excluded from this no-retry launch. Newly
fetched transcripts outside the original frozen selection do not extend it.

The finite maximum is 27,668 Luna/high extraction calls through the existing
Codex ChatGPT subscription, sending full frozen source text and the unchanged
structured template to OpenAI. There are no additional model-review calls,
provider fetches, automatic retries, API billing fallback, or recurring-unit
changes. Concurrency remains three, with a 1,200-second per-request deadline.
Completed originals and automatic quality assessments publish incrementally
through the established replay-safe company.transcript.structured publisher in
the fixed company.sqlite store. Network work precedes each short write lock.

The existing nine pinned source files still match the five-case pilot baseline,
whose 53 focused tests passed. Database storage readiness was checked again.
The private preparation helper verifies original source/request bindings and
freezes versioned Luna request identities without changing the old Terra plan.
The runner retains its existing pause after three transport failures and on
input/publication failures. No quota credit reset or automated restart is added.

This entry records authorized preparation, not a successful launch.
Evidence: .local/transcript-luna-universe-20260913/inventory.json,
database-output-baseline.json, resume.py and preparation-progress.json.


### Luna remainder prepared; automatic approval launch block

All 27,668 original remainder inputs passed source/request verification at
2026-09-13T16:51:02.836995Z. Frozen Luna/high plan:
7e2dc1bbee29f32c8364038824651a7e35d8d6ed890d13514c51d65ff6f57c91.
The existing 53-test validated source pins still match. Preparation made no
model or provider call.

Automatic approval review rejected the live launch before process creation.
A read-only proof then verified the earlier explicit full-text transfer approval
and that every new input is an unchanged subset of the original 27,796-source
approved payload. The same launch was reconsidered with that evidence and
rejected again: automatic review still requires explicit trusted user approval
of the sensitive full-transcript payload to OpenAI for this Luna run. No bypass,
indirect launch, model request or database publication was attempted. The
prepared batch is intact, with no started.json or run.log created.

The remaining question is explicit approval to send the full text of 27,668
remaining transcripts to OpenAI through the existing Codex subscription for at
most 27,668 Luna/high calls and save their outputs in the company database.
No additional model reviews, retries, API fallback, provider calls or recurring
changes are included. Both launch rejections remain recorded.

Evidence: .local/transcript-luna-universe-20260913/prepared-plan.json,
preflight.json, launch-block.json, transfer-scope-proof.json,
launch-reconsideration-block.json and RUN_STATUS.md.


### Explicit Luna remainder transfer approval - September 13, 2026 UTC

The user replied "approve" to sending the full text of the prepared 27,668
remaining transcripts to OpenAI through the existing subscription, using at
most 27,668 Luna/high calls, and saving the outputs to the company database.
This is explicit transfer approval for the unchanged frozen plan
7e2dc1bbee29f32c8364038824651a7e35d8d6ed890d13514c51d65ff6f57c91.
The prior launch rejection evidence is preserved. The private launch helper now
requires this exact approval receipt before execution. Scope remains three
concurrent requests, zero automatic retries, zero model-review/provider calls,
and no API fallback or recurring-unit change. This entry records approval;
actual launch and initial publication must be verified separately.

Evidence: .local/transcript-luna-universe-20260913/explicit-transfer-approval.json.


### Luna remainder launched after explicit transfer approval

The unchanged Luna/high remainder plan
7e2dc1bbee29f32c8364038824651a7e35d8d6ed890d13514c51d65ff6f57c91
launched at 2026-09-13T16:54:38.885492Z as PID 1073725, after the user's explicit
transfer approval. The process is running the existing validated universe runner
through the private progress wrapper. The finite maximum remains 27,668
extraction calls for 1,217 tickers, concurrency three, no retries or extra review
calls. Its continuously updated readable status is
.local/transcript-luna-universe-20260913/RUN_STATUS.md.

This records a verified launch, not completion of the population. Initial
publication validation is recorded separately when an output completes.
Evidence: .local/transcript-luna-universe-20260913/started.json,
authorization.json, explicit-transfer-approval.json and launch-verification.json.


### Initial Luna remainder publications verified

At 2026-09-13T16:56:54.027823Z, the first three resumed Luna/high outputs and
automatic assessments passed locked immutable database verification. Exact
original model bytes, structured JSON, and source snapshots match their private
publication evidence; all three passed Luna runtime validation. The database
contained 364 structured outputs at that check, with all 361 prior semantic
identities preserved and zero foreign-key violations in both structured tables.

The finite batch remains running. This is an initial publication check, not
full-population completion. Progress, remaining count, and any pause are updated
in .local/transcript-luna-universe-20260913/RUN_STATUS.md and the pinned plan's
progress.json. No further model reviews or retries have been added.

Evidence: .local/transcript-luna-universe-20260913/initial-publication-audit.json
and handoff-status.json.


## User-requested 50-worker Luna continuation - September 13, 2026 UTC

The user explicitly requested increasing concurrent Luna extraction from three
to 50 workers. This supersedes the previous three-worker operational setting for
the already approved original Luna remainder. It does not add sources, calls,
reviews, retries, destinations, credentials or recurring execution.

The existing coordinator received parent-only SIGINT; its native model children
finished normally. All 22 completed outputs and their exact source/response
evidence were verified in the database, with 383 total outputs, all 361
pre-batch identities preserved, and zero structured foreign-key violations.
No request is repeated.

The runner now accepts a bounded execution-only concurrency override of 1–50.
Defaults and frozen plans remain unchanged; the same plan ID, source pages,
Luna/high prompt/schema, request identities and 27,668-call total cap are reused.
The new invocation has at most 27,646 unconsumed calls and 50 workers. Model
requests still precede short existing physical-store publication locks. The
existing three-transport-failure pause drains in-flight calls without retries.

All 50 focused offline tests passed in 65.799 seconds, including 50 overlapping
workers with temporary-store publication/replay, invalid bounds before access,
and draining 50 failed calls without starting remaining work or retrying.
The final concurrency-only diff was reviewed. Existing source pins are unchanged
except the bounded runner update; no registry, migration, provider, schema,
model prompt, shared locking primitive or native transport changed.

This entry records readiness for the requested increase, not a successful live
50-worker launch. Evidence: .local/transcript-luna-universe-20260913/concurrency-50/
before-increase.json, drain-request.json, drain-audit.json, validation.json,
source-before.json and fifty.py.


### 50-worker continuation running and initial publication verified

The same approved plan resumed at 2026-09-13T17:09:18.891804Z as PID 1087842.
At 17:09:55 UTC, 50 simultaneous native gpt-5.6-luna extraction processes were
observed, with 50 in-flight entries reported by the coordinator. Available
host memory at that observation was approximately 3,268 MB; the native
processes' combined proportional memory was approximately 793 MB.

At 2026-09-13T17:10:34.683250Z, 22 new outputs had been saved since the increase,
giving 405 database outputs. Five new outputs and their automatic assessments
were verified against exact original responses and source snapshots; all five
passed Luna/high runtime validation. All 383 outputs present before the
increase were preserved, with zero structured foreign-key violations.

Two early calls ended with a disconnected response stream/network decoding
error. Retained error events reported no explicit throttling or quota error.
Those attempts are not retried. The pool continued processing and publishing.

Latest handoff at 2026-09-13T17:13:18.369609Z: 125 outputs saved since the increase,
50-worker setting, status running, 2 failed calls recorded. The finite total
call cap remains 27,668, and no completed transcript is repeated. This is
ongoing batch execution, not full-population completion.

Evidence: .local/transcript-luna-universe-20260913/concurrency-50/started.json,
authorization.json, initial-running-snapshot.json, initial-publication-audit.json,
handoff-status.json and RUN_STATUS.md. The existing parent RUN_STATUS.md is
also updated continuously by the 50-worker progress callback.


## Explicit resume of paused Luna batch - September 13, 2026 UTC

After being told that the 50-worker run paused after three consecutive network
stream disconnects, the user requested "Can you resume now?" This authorizes
continuation of the same approved frozen Luna/high plan at 50 workers, selecting
only its 16,443 still-unattempted transcripts. The 11,152 successful and 73 failed
attempts are terminal and skipped. The original 27,668 total call maximum,
full-text transfer approval, subscription destination, model, prompt/schema and
database publisher remain unchanged; there are no retries or additional reviews.

Preflight verified that the previous coordinator exited, all 11,225 attempted
entries were settled, and the pending entries had no prior request receipt.
Every completed record's semantic identity was present in the database, with
11,513 total outputs and zero structured foreign-key violations. All validated
source pins still match the 50-test baseline; no production code change or
test rerun was necessary. The prior paused progress/result are preserved.

The private continuation directory is
.local/transcript-luna-universe-20260913/resume-20260913-evening/.
Its authorization.json, preflight.json and database-output-baseline.json record
this scope. Once launched, active-run.json at the parent and concurrency-50
directories identifies the latest invocation, without replacing historical
started.json or run.log evidence. Read the active pointer for subsequent status
checks. This entry records preparation and authorization, not a successful launch.


### Evening Luna checkpoint resume launched and publication verified

The user-authorized continuation launched at 2026-09-13T22:37:15.501292Z as
PID 4082042. Fifty simultaneous native Luna processes were observed at
22:37:53 UTC. It reuses the same plan and requests, skips all terminal
attempts, and publishes new structured outputs incrementally.

At 2026-09-13T22:38:36.141494Z, the first 21 new outputs were present in the
database, for 11,534 total outputs. All 11,513 pre-resume semantic identities
were preserved. Five new outputs and their assessments passed exact original
response/source and Luna/high runtime checks; structured foreign-key checks
returned zero violations. There were no new failed attempts at this audit.

The batch remains running, not complete. For status, read
.local/transcript-luna-universe-20260913/active-run.json for the current
invocation and use its log_path and script_path. Historical started.json and
run.log under concurrency-50 still identify the previous stopped invocation.
Live RUN_STATUS.md files are updated by the current callback.

Evidence: .local/transcript-luna-universe-20260913/resume-20260913-evening/
started.json, launch-verification.json, initial-publication-audit.json and
handoff-status.json.

## 2026-09-13 local transcript consumer tools

The user authorized implementation of three local read-only tools so other
projects on this computer can retrieve retained raw transcripts and saved
structured extractions. Registry `2.86.0` / catalog `2.32.0` exposes them
through the existing absolute-path launcher, with exact `2.85.0`
predecessor recovery and unchanged prior contracts. See the
[consumer instructions](../LOCAL_AGENT_TOOLS.md#retained-transcripts-and-structured-extractions)
and [tool contract](TOOL_PLATFORM_SPEC.md#2026-09-13-retained-transcript-tools).

A finite public-launcher smoke check from `/tmp` made three company reads:
AAPL capture search, two source speaker turns, and one structured draft with
its first assessment page. Search returned 27 captures; the selected source
was AAPL fiscal 2020 Q1. All three calls succeeded without provider/model
requests. The original draft was automatically accepted and still unreviewed;
this is recorded assessment evidence, not a new approval.
Local receipts are in `.local/transcript-tools-20260914/`.
This implementation grants no new collector, model, scheduler, migration,
network-hosting or operational-store connection authority. Existing normal
clock execution retains only its previously recorded scope.


## User-requested increase to 100 Luna workers - September 14, 2026 UTC

The user explicitly requested another 50 concurrent Luna workers, for 100 total.
This supersedes the 50-worker runtime setting for the same approved original
batch. The source population, full-text destination approval, Luna/high profile,
prompt/schema, request identities, company publisher and 27,668 total-call
maximum remain unchanged. No retries, additional reviews, provider fetches,
API fallback, recurring units or agent-routing configuration were added.

The 50-worker coordinator PID 4082042 received parent-only SIGINT. Its in-flight
calls finished before exit. The settled checkpoint has 19,166 stored outputs
and 116 failed terminal attempts (19,282 calls consumed), leaving exactly 8,386
unattempted entries. Every completed record's semantic identity was verified
against the database. All 11,513 outputs in the previous baseline were
preserved; the database now contains 19,527 structured outputs, with zero
structured foreign-key violations. No pending entry has a previous request.

Production changes only raise the bounded runtime override from 50 to 100 and
update its error/help text. Frozen plans and the default worker count remain
unchanged. The 100-worker overlap/publication/replay test passed. Of 50 initial
focused tests, 49 passed; one failure fixture incorrectly assumed no replacement
could start before the third failure was observed. The fixture was corrected to
allow at most two replacement calls before that threshold, retain unattempted
work and prove no retries, and its targeted recheck passed. All 50 current test
cases therefore have passing evidence. No production failure logic changed.
The initial test failure and successful recheck remain recorded.

The private 100-worker launcher is prepared for at most 8,386 new calls under
the unchanged plan. It preserves the old invocation and publishes a new
active-run.json pointer on launch. This entry records readiness, not live
100-worker execution. Evidence is under
.local/transcript-luna-universe-20260913/concurrency-100/:
before-increase.json, drain-request.json, drain-audit.json, first-test-run.json,
validation.json, source-before.json and hundred.py.


### 100-worker continuation running and initial publications verified

The unchanged plan launched with a 100-worker override at
2026-09-14T02:15:50.007297Z as PID 2024242. At 02:16:34 UTC, exactly 100
simultaneous native Luna processes were observed. Their combined proportional
memory was approximately 1,495 MB, with approximately 3,501 MB host memory
available. The coordinator reported 100 in-flight entries.

The locked immutable audit at 2026-09-14T02:17:40.598441Z verified 67 new database
outputs since the increase, for 19,594 outputs total. All 19,527 pre-increase
output identities were preserved. Five new outputs and their automatic
assessments passed exact original response/source and Luna/high runtime checks;
structured foreign-key checks returned zero violations. No new call failure
had occurred at that audit.

At handoff (2026-09-14T02:20:45.793182Z), the same batch remains running with 100-worker concurrency:
312 outputs saved since the increase and 1 new failed calls. No extra
population, retries, review calls, provider fetches or API fallback were added.
This is ongoing extraction, not full-population completion.

Use .local/transcript-luna-universe-20260913/active-run.json for the current
invocation. It now identifies concurrency-100/hundred.py and that directory's
run.log; historical invocation receipts remain intact. Live status files also
show the current 100-worker run.

Evidence: .local/transcript-luna-universe-20260913/concurrency-100/started.json,
authorization.json, initial-running-snapshot.json, initial-publication-audit.json
and handoff-status.json.


## User-requested return to 50 Luna workers - September 14, 2026 UTC

After the 100-worker invocation paused on repeated HTTP 429 responses and
finished draining at 02:51 UTC, the user requested: "ok can you resume with
50 workers?" This explicitly supersedes the 100-worker runtime setting for
the same approved frozen plan
7e2dc1bbee29f32c8364038824651a7e35d8d6ed890d13514c51d65ff6f57c91.
The continuation permits at most 5,746 new Luna/high extraction calls, skipping
21,792 stored outputs and 130 terminal failed attempts. The original 27,668
total-call maximum, full-transcript transfer approval, existing subscription,
source population, structured template, publisher and failure guard are unchanged.
No retry, review, provider fetch, API fallback or recurring-unit change is added.

Preflight verified that the old coordinator had exited, all completed records
were settled and present in the database, and every pending entry lacked a
previous request receipt. The database contained 22,153 structured outputs with
zero structured foreign-key violations. The validated production source pins
were unchanged; the existing focused-test evidence was reused. Only the private
continuation helper, run evidence and this operating entry were changed.

The continuation launched at 2026-09-14T03:51:21.874487Z as PID 2722186.
Exactly 50 simultaneous native Luna workers were observed at 03:52:01 UTC.
The locked immutable audit at 03:53:02 UTC verified 39 new saved outputs
(22,192 total), all 22,153 prior identities preserved, and five exact original
response/source/structured-output and automatic-assessment comparisons. Structured
foreign-key checks returned zero violations. No new failed call had occurred
at that audit. This records ongoing extraction, not population completion.

The current pointer is .local/transcript-luna-universe-20260913/active-run.json.
Live readable progress remains in that directory's RUN_STATUS.md.
Evidence: .local/transcript-luna-universe-20260913/resume-20260914-50/
authorization.json, preflight.json, helper-validation.json, started.json,
launch-verification.json and initial-publication-audit.json.


## September 15, 2026 UTC - KBH Equibles recovery completed

The user requested retrying the blocked KBH ticker and advancing past null
transcripts. The finite recovery used eight new GETs (one retry each for
uncertain KBH 2024 Q3/Q4 plus remaining KBH history), 452,574 bytes, and the
existing key, company publisher and locks, within an eight-call / 64 MiB /
15-minute cap. Two saved pages were recovered without GETs. Original uncertain
attempts and all prior-day quota charges remain preserved.

All eight requests returned valid transcript pages. KBH completed with 26
transcripts and no unavailable-transcript gaps. The checkpoint is unblocked
at KBR: 28,411 total transcripts and 1,339 completed mapped histories.
September 15 UTC usage is eight charged / 9,992 remaining from fresh headers.
The existing timer is enabled and waiting for September 15 at 20:10 EDT;
no recurring unit was started, reset or reconfigured.

The collector now records explicit null first-page responses as retained
transcript gaps and advances, including validated zero-count data:null
envelopes. Transport failures, invalid quota/identity metadata and null after
partial pages remain blocked. No canonical empty call is published.

All 91 focused tests passed (six new null cases); the final immutable audit
preserved 28,401 prior transcripts/pages, validated ten new bundles and
lineage, and found zero transcript foreign-key violations. Both retried
quarters replayed with zero GETs and zero canonical writes. Migration,
selection, old quota and unit evidence were preserved. Full suite not repeated.
Details and retained evidence are in the
[dated transcript record](EQUIBLES_TRANSCRIPT_BACKFILL_2026-09-07.md#september-15-2026-utc---kbh-request-recovery-and-explicit-null-continuation)
and data/.operations/equibles-kbh-recovery-20260915/.


## September 15, 2026 UTC - Equibles service manually started

The user explicitly requested starting the Equibles fetcher. The existing
quant-data-equibles-transcripts.service was started once at
2026-09-15 03:31:29 UTC (September 14 at 23:31:29 EDT), beginning at KBR.
The eight prior UTC-day calls remain charged, leaving at most 9,992 new
requests under the existing 10,000 shared daily ceiling. The unchanged
controller permits four callers, six hours, 4 GiB and 24 inner invocations,
with no automatic failure retries. Existing credentials, selection,
checkpoint, publisher and physical locks are reused; timer cadence and
service configuration were not changed.

At 2026-09-15T03:32:48.379819Z the service process was running. The checkpoint
reported 36 new charged attempts,
30 new transcripts, 28441 total
transcripts, 1340 completed histories and
KC underway, with no terminal failure recorded.
This is startup/progress evidence, not a completed-run claim. The existing
oneshot service remains activating/start while collection runs.

No implementation changed since the 91 focused checks and KBH integrity/replay
audit, so tests were not repeated. The exact authorization, pre-start checkpoint
and status receipts, unchanged source/binding/registry/unit hash checks, and
startup verification are under
data/.operations/equibles-service-start-20260915/.


## Explicit 50-worker Luna resume - September 15, 2026 UTC

After the previous invocation paused on consecutive network response
disconnections, the user requested "can you resume?" This resumes the same
approved Luna/high plan at the retained 50-worker setting, with at most 3,937
new calls for its unattempted transcripts. The 23,579 stored and 152 failed
attempts are terminal and skipped. The original 27,668-call total cap,
full-transcript transfer approval, existing Codex subscription, source pages,
prompt/schema, company publisher and failure guard remain unchanged. No retry,
review, provider fetch, API fallback or recurring-unit action was added.

Preflight confirmed the old process had exited, all completed record identities
were present among 23,940 database outputs, and pending entries had no earlier
request receipt. Structured foreign-key checks passed. Validated production
source pins were unchanged; existing focused-test evidence was reused. The
private continuation helper passed syntax, preflight and final difference review.

The continuation launched at 2026-09-15T03:34:41.584621Z as PID 3281127.
Fifty native Luna processes were observed at 03:35:06 UTC. The locked immutable
audit at 03:35:58 UTC confirmed nine new outputs (23,949 total), all 23,940 prior
identities preserved, five exact original response/source/output and automatic
assessment comparisons, and zero structured foreign-key violations. There were
no new failed calls at that check. This is ongoing extraction, not completion.

Read .local/transcript-luna-universe-20260913/active-run.json for the current
invocation and RUN_STATUS.md beside it for live progress. Evidence is in
.local/transcript-luna-universe-20260913/resume-20260915-50/: authorization.json,
preflight.json, helper-validation.json, helper.diff, started.json,
launch-verification.json and initial-publication-audit.json.


## September 15, 2026 UTC - retained transport failure diagnostics

The service started at 03:31:29 UTC stopped at 03:35:46 UTC on KFY
2025 Q1/Q2. Attempts 121 and 122 have reservations but no retained response
or cancelled-dispatch marker. The run added 104 transcripts, reaching
28,515 total and 1,344 completed histories; UTC-day usage reached 122 charged
attempts with 9,878 remaining. The preceding KFY 2024 Q3/Q4 responses were
HTTP 200 and remain retained. The original worker discarded exception
details, so the exact transport cause remains unproven. No observed null
response explains this stop.

After the user challenged the earlier fix, local error handling was corrected:
the Equibles HTTP worker now reports an allowlisted failure category and any
received HTTP status, without response content, credentials or exception text.
The parent preserves typed timeout/connection/partial-response/worker failures;
serial and parallel acquisition retain failure receipts bound to the exact
attempt. Parallel settlement includes these diagnostics in its saved block
and recovers them without a GET or quota refund. Existing historical failures
receive no invented diagnostic. Null handling, request deadlines, quotas,
publication, automatic-retry policy and scheduler configuration are unchanged.

All 85 focused diagnostic, transport, parallel, empty/null, publisher and
daily-controller tests passed in 23.627 seconds. Eight new diagnostic tests
cover credential/error-text suppression, typed child-process messages,
persistent failure evidence, replay without retry/refund, and mismatched
receipt rejection. The full suite was not repeated for this bounded local
diagnostic fix. This change does not repair or prove the cause of the live
download failures and does not unblock KFY. The service remains stopped;
no provider call, credential read, operational-store access or restart was
performed for this diagnostic correction. Retry/continue behavior awaits a
separate user choice covering failed downloads.

Changed source: quant_data/operations/equibles_transcript_backfill.py and
quant_data/operations/equibles_parallel_backfill.py.
Focused tests: tests/operations/test_equibles_failure_diagnostics.py.
Baselines, full test log and validation receipt:
.local/equibles-failure-diagnostics-20260915/.

## Equibles retry-and-continue activation - September 15, 2026 UTC

The user's "ok go ahead" authorized one retry per failed download, then an
explicit unresolved coverage record and continuation of the existing finite
selected transcript backfill. The later "approve" authorized restarting the
unresponsive WSL host; WSL and the existing service session recovered. This
new decision supersedes the earlier no-automatic-download-retry restriction
for this backfill only. Hard validation/authentication/publication failures
still block; HTTP 429 remains quota deferral. Explicit null responses still
advance as recorded gaps, never complete transcript facts.

After 115 focused tests passed, cache-only recovery published the two retained
KFY pages with zero requests. The existing service started once at 04:18:09 UTC:
at most 9,878 new requests within September 15's shared 10,000 ceiling, four
callers, six hours, 4 GiB and 24 bounded invocations. No timer/unit configuration,
provider, credential, selection, store/schema, binding or registry changed.

Both uncertain KFY quarters succeeded on their single retry; KFY completed
with 27 transcripts. The 04:19:30 UTC live integrity check recorded 28,559 total
transcripts, 1,346 processed histories, KLIC underway, 170 charged requests,
9,830 remaining, two retry charges and zero unresolved coverage. It preserved
28,515 prior captures/manifests and verified all 44 additions, retained bytes,
lineage, migration ledger and transcript foreign keys. Both repaired quarters
replayed without requests or canonical writes. The service was still running
at 04:19:57 UTC; these are dated observations, not a current-state guarantee.

See [the Equibles operational contract](EQUIBLES_TRANSCRIPT_BACKFILL_2026-09-07.md)
for exact retry semantics, limits and validation. Receipts and pinned
source/binding/registry/unit hashes:
data/.operations/equibles-retry-continue-20260915/. Focused test log and source
baselines: .local/equibles-retry-continue-20260915/.

## Equibles user-requested resume - September 15, 2026, 14:56 UTC

The user requested starting Equibles again with its remaining quota. The saved
checkpoint had 4,709 charged requests, 5,291 remaining, 32,861 transcripts and
1,583 processed mapped histories, with NTST's four-request batch interrupted.
The existing source, publisher, binding, registry and unit pins matched the
previously validated version. WSL completed its normal boot and user-session
startup during the checks; no host restart, unit configuration change or timer
change was performed for this request.

One start of the existing service at 14:56:42 UTC resumed the same finite
selection. The new invocation is limited to at most 5,291 additional requests
within September 15's shared 10,000-call ceiling, four callers, six hours,
4 GiB and 24 bounded inner invocations. The existing credential resolver,
company.sqlite publisher, physical locks and one-retry/then-unresolved policy
are unchanged. Original charges were not refunded.

The 14:58:06 UTC progress check found the service running (PID 3275): 32,893
transcripts, 32 additions, 1,584 processed histories and NU underway. NTST
completed with 24 transcripts. Its retained first response was reused; the
three uncertain requests recovered on their single retries, attempts
4710-4712. All four retained response statuses were 200 and their saved byte
hashes verified. Prior completed histories, original attempt files, earlier
quota days and the frozen selection were preserved.

There were 36 new charged requests, 4,745 total charged and 5,255 conservatively
remaining; fresh provider headers were observed at 14:58:03 UTC. No unresolved
coverage was recorded. The checkpoint's ParallelReservation marker represented
the next active batch, not a terminal failure. These are startup/progress
observations, not a completed-run or full canonical-integrity audit.

Evidence: data/.operations/equibles-user-resume-20260915T145642Z/ contains the
authorization, original checkpoint/status/journal/attempts, start receipt and
progress verification. The prior 115-test evidence was reused because the
validated source pins were unchanged; no tests, commit or push were added.


## Luna interrupted-attempt recovery and 50-worker resume - September 15, 2026 UTC

The user requested "ok, can you pls resume?" after the previous coordinator
disappeared with 50 pending request receipts and no retained responses. This
continues the same approved plan at 50 Luna/high workers for its 2,700 untouched
entries only. The 50 interrupted calls are set aside alongside 154 earlier
failures, with all 24,764 completed batch outputs preserved. The original
27,668-call cap includes the 50 uncertain attempts; it is not reset.

The established single-item no-retry path settled the 50 pending attempts
locally, using a transport that forbids credential access and network calls.
It wrote terminal model_failed records carrying the existing uncertain-attempt
reason and one consumed call each, with zero new calls. Original pending
receipts and all 25,125 database output identities were unchanged. The old
running checkpoint was retained privately before reconciliation. The resulting
checkpoint has 24,968 accounted attempts, 204 failed/uncertain entries and
2,700 unattempted entries. Structured foreign-key checks passed.

A temporary-store check proved that this settlement makes no credential/model
request or canonical change, preserves the pending receipt, and permits only an
untouched entry to run afterward. Its first fixture attempt failed on an
incorrectly created private directory; the corrected fixture passed, and both
results are retained. Validated production source pins were unchanged. No
production code, prompt/schema, publisher, credential, provider, recurring unit
or retry policy was changed. Existing full-text transfer approval and the
subscription destination remain in force; no review call or API fallback was added.

The continuation launched at 2026-09-15T15:07:15.810509Z as PID 8316 with 50
in-flight slots. The 15:08:27 UTC locked immutable audit confirmed nine new
outputs (25,134 total), preservation of all 25,125 prior identities, five exact
response/source/output and automatic-assessment comparisons, and zero structured
foreign-key violations. There were no new failures at that check. This is
ongoing extraction, not completion of the batch.

The current pointer is .local/transcript-luna-universe-20260913/active-run.json,
with live progress in RUN_STATUS.md beside it. Evidence is under
.local/transcript-luna-universe-20260913/resume-20260915-recovery-50/:
interrupted-original-receipts.json, interruption-recovery.json,
interruption-recovery-check.json, initial-offline-check-failure.json,
authorization.json, preflight.json, helper.diff, helper-validation.json,
started.json, launch-verification.json and initial-publication-audit.json.


## Explicit 261-transcript Luna retry prepared; launch approval blocked - September 15, 2026 UTC

The original 27,668-entry batch finished at 16:21:54 UTC with 27,407 stored
outputs and 261 failed/interrupted attempts. The final locked immutable check
verified 27,768 database outputs including prior work, every stored batch
identity and automatic assessment, and zero structured foreign-key violations.

The user selected the 261 failures, including 50 interrupted earlier, and
requested retrying them. A separate frozen plan
7f0eb72e4753cad07127035657b19c88d7457094cf14954d7e7b19544f4cb2c6
selects exactly those 261 original captures across 173 tickers, for one new
Luna/high attempt each at 50-worker concurrency. All source, request and
configuration hashes match the approved original inputs; none already has
a database output. Separate pair/native evidence directories preserve 1,680
original evidence files. The consumed original cap is not reset. There is
no added review, subsequent retry, provider fetch, API fallback or recurring
unit change. Existing publisher, schema and production source pins are unchanged.

The temporary-store retry/replay check passed, proving one explicit new
attempt, preserved original failure evidence and zero extra calls/canonical
changes on replay. Preparation's first oversized baseline artifact was rejected;
the established compact identity baseline corrected it before any model call.
The private launcher passed syntax/readiness and final source review.

Automatic approval review rejected launch before process creation. A read-only
proof verified the prior explicit full-text approval and exact 261-source
subset. Reconsideration of the same command was also rejected: the gate requires
concrete user confirmation for this sensitive full-transcript transfer to OpenAI
after the risk was identified. Neither rejection was bypassed. No retry model
call, native worker or database output was created. The prepared work remains
ready pending that specific confirmation.

Evidence is under .local/transcript-luna-universe-20260913/retry-261-20260915/:
authorization.json, prepared-plan.json, preflight.json, offline-retry-validation.json,
helper-validation.json, original-evidence-hashes.json, transfer-scope-proof.json,
launch-block.json and launch-reconsideration-block.json. The parent
pending-retry.json identifies this prepared retry; active-run.json still identifies
the completed original batch.


## Explicit retry full-text approval and launch - September 15, 2026 UTC

After the prior launch rejections and the concrete full-text transfer question,
the user replied "approve" to sending the 261 original failed/interrupted
transcripts to OpenAI through the existing subscription, one new Luna/high
attempt each, 50 workers, with successful outputs saved to the database.
The explicit-transfer-approval.json receipt records this specific confirmation.
The private launcher now requires it before launch or execution; both prior
rejections remain preserved.

The unchanged 261-entry retry plan
7f0eb72e4753cad07127035657b19c88d7457094cf14954d7e7b19544f4cb2c6
launched at 2026-09-15T19:05:35.936815Z as PID 730852. The coordinator reports
50 in-flight slots. An initial process snapshot observed 49 native workers
while one slot was transitioning. The initial locked immutable audit at
19:07:02 UTC confirmed 11 new saved outputs (27,779 database total), all 27,768
earlier output identities preserved, five exact response/source/output and
automatic-assessment comparisons, and zero structured foreign-key violations.
No retry had failed at that check. This is ongoing retry execution, not its
completion.

The original failed receipts remain immutable in their original directories.
Retry receipts are under model-pair-pilots-retry-261-20260915, native receipts
under codex-retry-261-20260915, within the existing transcript-analysis state
root. Active-run.json includes these paths, the retry plan and its parent plan.
Its maximum is 261 new calls; no subsequent automatic retry, review, provider
fetch, API fallback or recurring change is added.

Evidence: .local/transcript-luna-universe-20260913/retry-261-20260915/
explicit-transfer-approval.json, post-approval-helper-validation.json,
retry.py.before-transfer-approval, started.json, launch-verification.json and
initial-publication-audit.json. Existing offline retry/replay and production
source-pin evidence was reused; the approval-only helper addition passed
syntax, readiness and final source review.


### Explicit 261-transcript retry completed and verified

The approved retry finished at 2026-09-15T19:14:32.928376Z after exactly 261
new attempts: 258 successful outputs saved, three model failures, no untouched
retry inputs, and no active workers. The remaining failures are CSTL 2020 Q4,
VTRS 2021 Q2 and ZTS 2023 Q2; their retained native events report network/stream
disconnections. No further retry was made.

The final locked immutable audit at 19:16:10 UTC verified 28,026 total database
outputs, preservation of all 27,768 prior identities, and exact original
responses, source snapshots, structured JSON and automatic assessments for all
258 recovered outputs. All 261 native receipts confirm Luna/high and subscription
billing. All 1,680 protected original evidence files remain byte-identical;
structured foreign-key checks returned zero violations.

Automatic assessments on the 258 recovered drafts are 30 accepted, 180 accepted
with flags and 48 blocked. These are retained automated judgments, not new
independent factual reviews. The user-approved one-attempt retry round is
complete with the three failures above. The original 27,668-attempt evidence
and both earlier approval rejections remain preserved.

Evidence: .local/transcript-luna-universe-20260913/retry-261-20260915/result.json,
final-audit.json and completion-receipt.json. Current active-run.json identifies
the completed retry; pending-retry.json records completed_with_failures.


## New 9,873-transcript Luna batch prepared; launch blocked - September 15, 2026 UTC

After an exact membership inventory identified 9,873 new transcripts outside
the original 28,030-capture universe, the user requested starting their
extraction. The prepared plan
80b292daff77c5764d464c9d435f13612a12597a86d6dfbc1253f3dfc661af4b
selects exactly those 9,873 unextracted, unattempted captures across 476 tickers.
The four older unresolved calls remain excluded: ADM 2026 Q2, CSTL 2020 Q4,
VTRS 2021 Q2 and ZTS 2023 Q2. The finite workload is at most 9,873 Luna/high
calls with 50 workers, unchanged structured template and incremental company
publication. No review, automatic retry, provider fetch, API fallback or
recurring change is included.

Preflight verified 28,026 prior structured outputs, zero structured foreign-key
violations, all 9,873 source/request bindings, and no previous request/native
attempt for any selected capture. Validated production source pins are unchanged;
existing focused-test evidence was reused. The private launcher passed syntax,
readiness and final source review. No production code, schema, prompt or
publisher changes were made.

Automatic approval review rejected the launch before process creation because
the previous explicit full-text transfer approval covered the earlier
261-transcript retry rather than this new 9,873-transcript payload. No workaround
or indirect launch was attempted. The batch is ready, but no new model/provider
call or database publication has occurred. The missing confirmation is specific
approval to send these full transcripts to OpenAI through the existing
subscription for the stated finite extraction workload and database storage.

Evidence: .local/transcript-luna-new-9873-20260915/authorization.json,
inventory.json, selected-captures.json, excluded-prior-unresolved.json,
database-output-baseline.json, preparation-progress.json, prepared-plan.json,
preflight.json, helper-validation.json and launch-block.json.
The older batch directory's pending-new-batch.json identifies this prepared
batch. Its active-run.json still identifies the completed 261-transcript retry;
RUN_STATUS.md now shows the pending new extraction batch.


## September 15, 2026 UTC — completed 25-ETF missing-price repair

The user's "pls fix" authorized the gaps confirmed in the preceding 25-ETF
audit: August 17–September 1 for 23 ETFs, August 28–September 1 for IWM,
and September 14 for all 25. Exactly 49 FMP full-EOD requests succeeded with
zero retries and 69,274 retained bytes. The finite run reused the existing
credential resolver, shared allowance, active selection, parser, canonical
market publisher and physical locks; it added 49 captures and 304 missing
price versions/current rows. No recurring unit, binding, registry, migration,
production code or schedule changed. This completed population has no repeat
authority.

The final immutable audit verified all 304 original-response OHLCV/source-row
comparisons and lineage references, preserved all prior target prices, versions,
capture metadata, identities and migration ledger, and proved 49 exact replays
write nothing. Thirty focused tests and a bounded temporary-store
publication/replay/invalid-response check passed. The public verification at
19:45:45 UTC returned all 25 ETFs with 9/9 August features, 225/225 values,
and complete sessions through September 14. The original pre-repair cutoff
still returned exactly its original 33 features. Historical publication and
common adjustment-vintage warnings remain unchanged.

The [repair record](ETF_PRICE_GAP_REPAIR_2026-09-15.md) records scope, limits,
validation and local verification corrections. Private evidence is under
.local/etf-gap-repair-20260915/; original response and publication receipts
are under data/.operations/etf-gap-repair-20260915/. No pending provider work,
test or verification remains for this repair.


## New 9,873-transcript batch explicitly approved and running - September 15, 2026 UTC

The user explicitly approved the concrete question covering full-text transfer
of all 9,873 selected transcripts to OpenAI through the existing subscription,
at most 9,873 Luna/high calls, 50 workers, and incremental database publication.
This new approval covers the payload absent from the earlier blocked launch;
the original launch-block evidence remains unchanged. The private launcher
now verifies this exact approval before readiness or execution. Its syntax,
readiness, production source pins and final approval-guard diff passed;
existing focused test evidence remains applicable.

The batch launched at 2026-09-15T19:50:29.155643Z with coordinator PID 837178.
Live inspection confirmed 50 native Luna workers. The unchanged frozen plan is
80b292daff77c5764d464c9d435f13612a12597a86d6dfbc1253f3dfc661af4b.
It contains 9,873 new captures across 476 tickers, excluding the four older
unresolved calls. No automatic retries, extra model reviews, provider fetches,
API fallback or recurring-unit changes are authorized by this launch.

The locked immutable audit at 19:52:27 UTC found 49 new outputs, 28,075 total,
and all 28,026 prior output identities preserved. Five retained responses,
source snapshots, structured outputs and automatic assessments matched their
database rows exactly. Their native receipts confirmed Luna/high and ChatGPT
subscription billing; structured foreign-key checks returned zero violations.
At that checkpoint the batch was running with 50 in flight, one failed model
attempt and 9,823 unfinished inputs. Automatic assessments on the 49 saved
drafts were five accepted, 32 accepted with flags and 12 blocked; these retained
draft assessments are not independent factual reviews. This is initial
publication evidence, not batch completion.

Evidence: .local/transcript-luna-new-9873-20260915/
explicit-transfer-approval.json, run.py.before-transfer-approval,
post-approval-helper-validation.json, started.json, launch-verification.json
and initial-publication-audit.json. The original batch directory's
active-run.json and pending-new-batch.json now identify this running batch;
RUN_STATUS.md is updated incrementally in both batch directories.

## September 15, 2026 - weekly historical-price repair authorized

The user explicitly approved implementing a weekly historical-price-only
missing-price repair and activating it Saturday at 02:00 America/Toronto.
This authorizes the new quant-data-weekly-price-repair.timer, limited each
week to the latest completed Monday-Friday window of the existing daily-price
population, at most 3,000 FMP full-EOD requests, 256 MiB and 60 minutes, no
automatic retries and the existing shared provider allowance. Persistent
catch-up covers the latest due week only; no unbounded older backfill or manual
provider run is authorized. Existing prices must not be overwritten.

The [weekly repair contract](WEEKLY_PRICE_REPAIR_2026-09-15.md) records the
mechanics and known calendar/listing coverage limits. The read-only preflight
pinned 2,359 existing symbols and made zero provider requests. The first eligible
slot is September 19, 2026 at 02:00 Toronto. Activation is pending final
validation; a later dated receipt will record actual installed state.


## September 15, 2026 - weekly repair activation held for full validation

The user's later explicit decision was "Keep inactive; continue full
validation." This holds the earlier activation approval while validation is
completed. The new weekly service/timer are linked for inspection only; the
timer is inactive and not enabled, with no service start. Its prepared
activation guard pins the first eligible September 19, 02:00 Toronto slot.
No weekly provider request or canonical price write has occurred.

The full suite is being rerun from a hashed source-only temporary copy after
the original live-data metadata guard exhausted host memory. The isolated run
has no operational data, credentials or external network. Two stale test
expectations were corrected without changing production behavior or frozen
registry evidence; eight focused checks passed and independent follow-up
review found no blocker. Complete-suite results remain pending.

## September 16, 2026 UTC - Luna remaining extraction prepared; launch blocked

The user requested launching Luna for the remaining transcripts after restarting
WSL. Read-only preflight found the existing 9,873-input plan paused after 936
stored outputs and 18 failed calls, leaving exactly 8,919 untouched entries.
All 28,962 database output identities were preserved in the preflight baseline;
structured foreign-key checks passed. Production source pins are unchanged and
the earlier 50-test evidence remains applicable.

A further 605 unattempted inputs outside that plan were frozen using source
captures through 00:30:50.380749 UTC. Four of those finished publication during
the initial inventory, whose earlier snapshot counted 601 new inputs. The
additional prepared plan is
9cabc5845145f500aeaa2e679cd16358b33ca23cf01f9d7cc6c4970fab11e3bf.
The four older unresolved captures remain excluded, for 22 failed captures
excluded in total. No continuously expanding population is included.

The private resume launcher passed syntax/readiness and seven offline approval
guard checks. It reuses 50 Luna/high workers, the fixed subscription adapter,
incremental company publication, source identities and no-retry policy. The
additional 605 cannot start without a separately recorded full-text approval;
if approved, they follow the resumed plan only after it finishes normally.

Automatic approval review rejected the attempted 8,919-input resume before
process creation, despite the saved earlier batch approval. Its stated reason
was that the current request authorized extraction generally but did not
explicitly authorize sending this full sensitive payload to the external
OpenAI subscription and writing derived results. No workaround was attempted.
No new model request or database publication occurred. A current explicit
confirmation is required for the entire prepared 9,524-transcript queue,
at most 9,524 calls, using the existing subscription and 50-worker setting.

Evidence: .local/transcript-luna-new-9873-20260915/resume-20260916/ contains the
preflight, preserved baseline and records, both fixed selections, additional
plan, one-use launcher, helper validation and launch-block.json. Preliminary
read-only evidence scripts encountered an incorrect output-column assumption
and an oversized JSON snapshot; these were corrected by using actual schema
fields and a compact identity baseline. A changing source inventory was
resolved by freezing the capture cutoff. No production code or tests changed.

## September 16, 2026 UTC - explicit remaining-queue approval and running Luna extraction

The user replied "approve" to sending the full text of all 9,524 prepared
transcripts to OpenAI through the existing Codex ChatGPT subscription, at most
9,524 Luna/high calls with 50 workers and successful outputs saved to the fixed
company database. This explicitly covers the 8,919 untouched inputs in the
existing plan plus the separately frozen 605-input follow-on. The original
launch-block evidence is preserved; no additional full-text confirmation is
pending for either selection. Previously failed inputs remain excluded.

The validated launcher started at 00:41:24.831605 UTC as PID 27226. Fifty native
Luna workers were observed. The first locked immutable audit verified 51 new
database outputs, preservation of all 28,962 prior identities, five exact
retained-response/source/output/assessment comparisons, pinned Luna/high
subscription receipts and zero structured foreign-key violations.

After 70 calls, a backward wall-clock reading of approximately one second
triggered the existing timestamp guards for KWR 2022 Q2 assessment and KWR
2021 Q3 publication. Both responses completed at about 00:42:41.7 UTC, while
the subsequent failure checks read about 00:42:40.8 UTC. No timestamp was
clamped, fabricated or backdated. The batch drained and paused with 67 newly
stored outputs, these two retained successful responses and one failed model
call. The guards and production collector code remain unchanged.

Once current time was later than the retained evidence, both KWR saves were
recovered through the existing preparation and publisher paths with a
transport factory that forbids model calls. Their original failure records
were retained; request, response and native-start receipt bytes were unchanged.
Two exact replays returned unchanged with zero canonical writes. Recovery
used zero new model calls and raised the saved count to 69 for this approval.

The continuation started at 00:49:38.581201 UTC as PID 48480, selecting only
the 8,849 untouched original-plan entries. The explicit cap remains
70 consumed + 8,849 untouched + 605 queued = 9,524 total calls. The follow-on
starts only after normal completion of the current plan; existing failure
guards can still pause the run. No model request is retried.

At 00:50:59.975473 UTC, the continuation audit found 19 more outputs, 29,050
database outputs total, and preserved all 29,031 identities present after
recovery. Five fresh exact response/source/output/assessment and subscription
receipt comparisons passed, with zero structured foreign-key violations.
The progress checkpoint was running with 50 in flight, 88 new stored outputs
since approval and one new failed model call. This is verified ongoing
extraction, not completion of all 9,524 inputs or independent factual review
of generated drafts.

Production source, prompt/schema, publisher and existing retry policy remain
unchanged. The private continuation made exact checkpoint/count/path updates
to the validated helper and added the explicit total-call-cap assertion;
syntax, readiness and final diff checks passed. Earlier 50-test production
and seven offline approval-guard checks were reused. No full suite, provider
fetch, API fallback, extra model review, scheduler change, commit or push was
performed.

Approval and initial run evidence:
.local/transcript-luna-new-9873-20260915/resume-20260916/.
Recovery, preserved failures, zero-write replay, continuation, live audit and
handoff evidence: its after-clock-recovery/ subdirectory.
The current pointer remains
.local/transcript-luna-universe-20260913/active-run.json, with live progress
in RUN_STATUS.md beside it.

## September 15, 2026 - weekly repair validation narrowed; activation remains held

The user's newer decision requests affected-component testing and a project
rule update, superseding the earlier task-specific full-suite requirement.
The weekly repair boundary is covered by 60 focused checks, independent review,
and unit/calendar verification. Implementation and focused test bytes are
unchanged from that validated snapshot. The interrupted full-suite attempts
remain recorded as interrupted, not passing evidence.

The broad test runs were stopped. Unrelated maintenance edits in 32 test files
and the Inspector registry guard were restored to their exact preimages, with
a separate applicable patch retained. The project validation rules now require
a concrete shared-invariant impact or current explicit gate before expanding
to the full suite.

The weekly timer was read back as ActiveState=inactive and UnitFileState=linked.
The existing activation hold remains in force. No weekly service start,
provider request, canonical publication, or expansion of operational authority
is part of this rule update. See the current validation checkpoint in the
[weekly repair record](WEEKLY_PRICE_REPAIR_2026-09-15.md).


<a id="recorded-weekly-price-repair"></a>

## September 15, 2026 - weekly repair Saturday schedule activated

The user's subsequent "OK go implement" resumes the requested activation
following the affected-component validation handoff. This supersedes the
earlier activation hold for quant-data-weekly-price-repair.timer only.
The existing weekly population, provider limits, missing-only policy, no-retry
rule, and first eligible September 19 slot are unchanged.

Verified activation readback: UnitFileState=enabled, ActiveState=active,
SubState=waiting, next trigger September 19, 2026 at 02:00 America/Toronto
(06:00 UTC). The service has no start timestamp and remains inactive; no
manual provider request or repair was performed. The timer requires the WSL
user scheduler to be running; it does not wake Windows or launch WSL.

The validated implementation and focused tests remain byte-identical.
The existing 60 passing focused checks and independent review were reused;
installed unit/link, syntax, first-slot, and calendar checks passed.
See the [weekly repair record](WEEKLY_PRICE_REPAIR_2026-09-15.md) and private
schedule-activation-20260915.json receipt for exact activation evidence.


## September 16, 2026 UTC - Luna publication recovery; requested continuation blocked

The user requested resuming the paused Luna extractor. Its one publication
failure was MIRM 2025 Q3: the wall clock had moved backward by less than one
second, so publication predated the retained response/assessment evidence.
The existing publisher recovered that retained result with zero model calls,
preserving all 30,973 prior output identities and all original evidence bytes.
An exact replay wrote nothing; structured foreign-key checks passed. The
database then contained 30,974 structured outputs, including 2,948 in this batch.

The prepared continuation selects only the 6,884 untouched original entries
and the already-approved 605-input follow-on. The original transfer cap is
unchanged: 2,035 consumed under that approval plus 7,489 remaining equals 9,524.
The 41 failed model/output attempts and four older unresolved inputs remain
excluded. Existing Luna/high, 50 workers, subscription transport, publisher,
prompt/schema, original source pins, and no-retry rules are unchanged.
The private helper passed syntax, exact mechanical diff review, source-pin
and readiness checks; existing focused validation was reused.

Automatic approval review rejected the launch before process creation,
stating that the generic resume request did not specifically authorize the
remaining full transcript payload and OpenAI destination. The saved original
explicit full-text approval remains preserved. No workaround, new model call,
or additional run was attempted; a current explicit confirmation is pending.
Evidence and the ready continuation are under
.local/transcript-luna-new-9873-20260915/resume-20260916/after-publication-pause/.


## September 16, 2026 UTC - approved Luna resume; provider capacity pause

The user explicitly replied "approve" to sending the remaining 7,489 full
transcripts to OpenAI through the existing Codex subscription, with at most
7,489 Luna/high calls, 50 workers, and successful results saved to the company
database. This resolves the preceding launch approval block for that finite
scope. The current approval is saved separately; original approvals and the
rejected attempt remain retained.

The prepared continuation launched at 03:15:15.777456 UTC as PID 587865 with
50 configured workers. It dispatched 52 previously untouched inputs and saved
43 new outputs. Eight calls failed because Luna reported that the selected
model was at capacity, and one failed after a response-stream disconnection.
The existing consecutive-failure guard stopped dispatch and drained the
in-flight work. At 03:18:42.398492 UTC the run was paused with zero in flight;
the coordinator exited. No failed request was retried or model switched.

This batch now records 2,991 stored outputs and 50 model/output failures,
with 6,832 untouched entries plus the 605-input approved follow-on still
queued. The current approval has 7,437 unused calls. The follow-on has not
started. The run ledger implies 31,017 total structured outputs; the bounded
database audit during draining directly verified 31,014 total, 40 of this
invocation's new outputs, preservation of all 30,974 prior identities, five
exact response/source/output/assessment comparisons, and zero structured
foreign-key violations. The final three saves are recorded in the run result.

No production code, schema, model configuration, retry guard, or scheduler
was changed. Prior focused validation was reused. Evidence:
current-explicit-transfer-approval.json, started.json, launch-verification.json,
initial-publication-audit.json, connection-failure-summary.json,
resume-result.json, and handoff-status.json under
.local/transcript-luna-new-9873-20260915/resume-20260916/after-publication-pause/.


## September 16, 2026 UTC - user-requested 20-worker Luna continuation

The user requested resuming Luna and reducing concurrency to 20 workers.
The existing full-text transfer and company-database publication approval is
preserved. This continuation is limited to 6,832 untouched original entries
plus the approved 605-input follow-on: 7,437 remaining calls, with 52 calls
already consumed from the latest 7,489-call approval. The 50 prior failed
model/output attempts and four older unresolved inputs remain excluded.

The private continuation changes only its invocation path, checkpoint counts,
and the user-requested concurrency. Production source pins, prompt/schema,
Luna/high subscription transport, publisher, and failure guard are unchanged.
Syntax, exact helper difference review, readiness, prior-record preservation,
and unattempted-input checks passed; existing focused validation was reused.
No full suite or scheduler change was needed.

The continuation launched at 2026-09-16T11:36:13.589958Z as PID 621509.
Readback observed 20 native Luna workers. The first bounded publication audit
verified 9 new outputs and 31026 total, preservation of all
31,017 prior identities, five exact source/response/output/assessment and
subscription-receipt comparisons, and zero structured foreign-key violations.
At the handoff snapshot the run status was running, with
20 in flight, 24 newly saved outputs, and
3 new unsuccessful model/output attempts. Failed attempts are not retried.
This is ongoing work, not completion of the remaining queue.

Evidence: authorization.json, concurrency-amendment.json, original transfer
approvals, helper.diff, helper-validation.json, preflight.json, started.json,
launch-verification.json, initial-publication-audit.json, and handoff-status.json
under .local/transcript-luna-new-9873-20260915/resume-20260916/workers-20/.
The current pointer and live status remain in
.local/transcript-luna-universe-20260913/active-run.json and RUN_STATUS.md.


## September 16, 2026 UTC - user-requested Equibles lock-timeout resume

The user requested resuming Equibles. The normal 00:10 UTC invocation stopped
at 03:07:39 UTC with exit 75 after a 60-second physical company-store lock
timeout while publishing VAC. Its four-response batch had settled successfully;
VAC 2020 Q1/Q2 were published and Q3/Q4 remained in the retained cache.
There was no quota exhaustion: 5,252 requests were charged and 4,748 remained.
The former lock holder was not established. A first immutable preflight rejected
a changing sidecar; a subsequent locked immutable read and full transcript
metadata baseline succeeded without bypassing the read safeguard.

This explicit decision authorized one start of the existing service for the
remaining 127 histories in the frozen 2,222-subject selection, with at most
4,748 additional requests on September 16 within the shared 10,000-call cap.
The existing EQUIBLES_API_KEY resolver, company.sqlite publisher, physical locks,
four callers, six-hour/4-GiB/24-invocation ceilings, UTC-midnight deadline, and
one-retry/then-unresolved policy were preserved. No unit or timer was changed.

Only the exact recorded lock-timeout block was cleared after preserving the
original checkpoint, journal, attempts and response receipts. Cache-only
recovery used zero provider requests and published the two complete retained
VAC calls. The locked immutable audit verified 42,889 canonical transcripts,
preserved all 42,887 earlier capture/page manifests, checked both added raw
bodies against retained bytes and ingestion lineage, found no transcript
foreign-key violations, and confirmed an unchanged migration ledger and quota.

The single service start at 23:11:25 UTC succeeded with PID 663459. At
23:12:10 UTC the running checkpoint had 42,911 transcripts (24 additions,
including the two recovered calls), 2,096 processed histories and VAL underway.
There were 27 new charged requests, 5,279 charged for the day and 4,721
conservatively remaining. Twelve fresh retained response receipts passed their
hash/byte checks; provider headers were observed at 23:12:08 UTC. The active
ParallelReservation marker represented an in-flight batch, not a terminal block.
Prior completed histories, frozen selection, original attempts and prior quota
days were preserved. This is a verified startup/progress snapshot, not completion
of the remaining population or a claim that future lock contention is fixed.

Production source, registry, binding and unit pins matched the September 15
validated activation, so its 115 focused test results were reused; no new tests,
production code, commit or push were needed. The private one-use recovery helper
passed syntax and review. Evidence: data/.operations/equibles-user-resume-20260916/
contains authorization, original state/receipts, split canonical baselines,
cache recovery and integrity results, service start receipts and running
postcheck. Helper: .local/equibles_user_resume_20260916.py.


## September 16, 2026 UTC - approved bounded retries for Luna extraction

The user requested adding retries to the Luna process. This newer scoped
decision supersedes the preceding no-retry rule for this continuation only.
The saved full-transcript transfer and company-database publication approvals
remain in force. Concurrency remains 20 Luna/high workers using the existing
Codex subscription. No other provider, scheduler, schema, prompt, credential,
or model settings changed.

The continuation covers 6,733 untouched original inputs and the previously
approved 605-input follow-on. Its total remaining call allowance is 7,338:
151 consumed plus 7,338 remaining equals the latest 7,489-call approval.
No earlier completed or failed inputs are reopened. Temporary capacity,
rate-limit, timeout and connection failures may receive two additional
attempts, after 30 and 120 seconds plus up to five seconds of jitter.
Each native attempt is durably charged before launch against the existing
plan ceiling; retries do not increase approved calls. Exhausted budget may
leave pending inputs, and the existing consecutive-failure guard can pause
the run after final failed attempts drain.

Implementation is opt-in in company/transcript_analysis_retry.py and the
universe batch coordinator; paired receipts now preserve model-attempt
counts. Existing callers retain no-retry defaults. The contract documents
the precise policy. Seventy distinct focused retry, adapter, batch, paired
receipt and history checks passed in an isolated source copy with temporary
stores. One synthetic fixture was corrected, and the affected module passed
again. Required source pins and final diff checks passed. No full-suite
trigger applied. Validation is retained under
.local/transcript-retries-20260916/.

The continuation launched at 2026-09-16T23:18:07.697140Z as PID 666129.
Readback observed 20 native Luna workers. The initial locked immutable
publication audit verified 3 new outputs and
31111 total, preservation of all 31,108 prior
identities and 3,140 prior terminal records, exact source/response/output
and assessment comparisons for 3 samples,
the expected subscription/model receipts, and zero structured foreign-key
violations.

At 2026-09-16T23:20:30.016376Z, the coordinator remained alive with
status running, 20 in flight,
21 newly stored outputs, and
0 automatic retries charged. This is startup verification,
not completion of the remaining queue or evidence of a live retry recovery.
The approved follow-on remains conditional on normal completion of the
original plan.

Evidence: retry-authorization.json, preserved transfer approvals,
preflight.json, helper-validation.json, helper.diff, started.json,
launch-verification.json, initial-publication-audit.json and handoff-status.json
under .local/transcript-luna-new-9873-20260915/resume-20260916/retries-v1/.
The existing active-run pointer and RUN_STATUS.md identify this continuation.


## September 17, 2026 UTC - newly saved transcripts added to Luna backlog

The user requested adding newly saved transcripts to the backlog and an ETA.
A locked immutable company-store snapshot at 02:45:25 UTC found 45,290 saved
source quarters and 34,028 extracted quarters. Comparing source quarter
identities with all five existing frozen plans selected exactly 6,782 new
inputs across 324 symbols, with capture cutoff 02:45:24.539245 UTC.
The saved local backlog selection excludes extracted quarters, existing plans
and all prior attempted inputs. No new model calls or canonical writes occurred.

The backlog totals 11,200: 3,805 unattempted original inputs, eight retained
model outputs awaiting publication, the approved 605-input follow-on and
6,782 newly queued inputs. Sixty-two older failed inputs remain excluded.
The active coordinator had paused at 02:44:00.931941 UTC after eight company
publication lock timeouts; it exited with zero in flight. Those eight native
outputs and prepared results remain retained. An initial inventory also timed
out on the store lock; the subsequent ordinary locked immutable read succeeded.
The exact holder during Luna's failures was not established. No collector or
recurring service was stopped or changed.

The recent 30/60-minute pace was 13.8-13.9 transcripts per minute with 20
workers: approximately 13.5-14 processing hours after resumption for the full
backlog, including about 8.1-8.2 hours attributable to the new selection.
This is conditional elapsed processing time, not a live finish commitment:
the run is paused and existing finite execution caps remain unchanged.
The new selection is queued locally, not started or automatically dispatched.

Validation checked unique capture/quarter identities, zero overlap with
existing plans, the source cutoff, unchanged hashes of all five frozen plans,
preserved prior queue fields, retained publication results and exact queue
readback. Source and canonical database contents were not edited, so no
executable test suite was required. A final output-rendering exception after
the successful writes was resolved by reading back the saved checked artifacts.

Evidence: .local/transcript-luna-backlog-20260917T024525Z/ contains the immutable
selection, inventory, rate samples, prior queue, publication-pending records,
backlog and checks. The discoverable index and readable summary are
.local/transcript-luna-universe-20260913/backlog.json and BACKLOG.md;
pending-new-batch.json preserves its original fields and links the new backlog.


## September 17, 2026 UTC - requested AVB, EA and EQR run; provider unavailable

The user explicitly requested an Equibles run for AVB, EA and EQR, the three
retained FMP equity identities outside the selected 2,248-member CSV. The
one-time workload was bounded to 300 shared charged attempts, 30 minutes,
128 MiB, the existing EQUIBLES_API_KEY resolver and company publisher.
No recurring unit, binding file, registry or production source changed.

The original selected history was complete: 2,222 mapped subjects and 45,290
transcripts. A locked immutable preflight found no saved AVB/EA/EQR transcripts,
verified their original retired-roster identities and captured a transcript
metadata baseline. Pure checkpoint conversion/restoration checks preserved
membership, completed records and quota. The historical activation configuration
was used to verify the frozen selection hash; current configuration changes
had not changed its membership or provider mapping. These preparations made
zero provider requests or canonical writes.

The first identity reservation was rejected by the history transport's local
route check before network dispatch. Its charge and diagnostic were retained,
with a separate not-dispatched reconciliation receipt. The existing bounded
identity helper then made one screener request at 02:45:20 UTC, returning
HTTP 200 with an empty result and notFound=[AVB, EA, EQR]. No positive provider
mapping was invented and no checkpoint selection transition was activated.

Three separately bounded direct earnings-call catalogue checks followed at
02:48:14-17 UTC, one per explicitly requested ticker. Each returned HTTP 404.
The run therefore added zero transcripts and made zero canonical writes.
Original checkpoint fields and the selected scope were unchanged except for
shared quota accounting. The complete status was refreshed cache-only with
zero provider calls. Retained response bytes matched all three saved hashes.

Total cost: four actual provider requests and five conservative local charges,
including the proved pre-dispatch rejection. September 17 accounting reached
996 charged and 9,004 locally remaining; the last provider header reported
9,005 remaining. There were no provider retries. Existing 115-test evidence
was reused for unchanged production pins; the private helpers passed syntax
and scoped preflight checks. No new suite, commit or push was needed.

Evidence: data/.operations/equibles-three-tickers-20260917/ contains authorization,
original checkpoint and split canonical baseline, helper validation, original
and continued launch receipts, route-validation reconciliation, identity
response, each direct catalogue response, direct result and completion.json.
Private helpers: .local/equibles_three_tickers_20260917.py and
.local/equibles_three_direct_catalogues_20260917.py. These results establish
unavailability under the three exact requested Equibles ticker routes at the
recorded time, not absence of historical transcripts from every possible source.


## September 17, 2026 UTC - Luna resilience implemented; expanded launch held

The user requested resuming the expanded backlog, a more forgiving database
lock timeout, and automatic continuation after a few minutes when paused.
Implementation selects five-minute waits and a three-minute delayed resume,
bounded at ten automatic resumptions per finite plan. Only recognized
temporary database/transport pauses qualify; permanent errors, uncertain
evidence and model-budget exhaustion stay held. The existing native retry
limit remains two retries per input. No recurring unit was changed.

The frozen continuation plan is
79a892b67c2ab4f7371ae5fb6a328ec2c10839a643744c367f2a8ce364bb762d,
containing exactly 11,192 untouched inputs: 3,805 original, 605 prior follow-on
and 6,782 newly queued. All request/native-start receipts were absent.
The new prepared plan has an explicit 33,576-call ceiling including retries;
older plans/caps are preserved. Sixty-two older failures stay excluded.
Model/destination/publication remain Luna/high, 20 workers, existing Codex
ChatGPT subscription and data/company.sqlite. No extra incoming population,
provider fetch, review call or API fallback is included.

At 03:21:32.116420 UTC the eight retained publication failures had been
recovered with zero model calls. Immutable validation confirmed 34,036 total
structured outputs, all 34,028 prior identities preserved, all 6,060 prior
terminal record bytes unchanged, all eight response/preparation receipts
preserved, eight exact output comparisons and no structured foreign-key
violations.

Fifty-eight focused checks passed in an isolated source copy with explicit
temporary stores and network disabled. Source pins, final diff, private helper
syntax, readiness and finite membership checks passed. No full-suite trigger
applied. An initial constructor-default typo was corrected before the passing
run; an oversized command failed before writing any file and was split safely.

Automatic approval review rejected the launch before process creation. Its
stated reason was that the resume request did not explicitly approve sending
the 11,192 potentially sensitive full transcripts to OpenAI. No workaround,
new model request or alternative launch occurred. A confirmation naming the
full payload, destination, 20 workers, 33,576-call maximum and company-database
publication is pending. The supervisor is implemented and ready, but not
active for this expanded plan.

Evidence under .local/transcript-resilience-20260917/: exact preimages,
impact/test-source/focused logs, validation.json, frozen source/continuation
plans, selection-checks.json, database baselines, preserved records,
cache-recovery.json, authorization.json, resume.py and launch-block.json.
The prior active-run pointer remains historical; pending-new-batch.json,
backlog.json and RUN_STATUS.md report the prepared blocked continuation.


## September 17, 2026 UTC - expanded Luna transfer approved and supervised run active

The user explicitly replied "approve" to sending the full text of the 11,192
queued transcripts to OpenAI through the existing Codex subscription, with
20 Luna/high workers, at most 33,576 calls including retries, and successful
outputs saved to the company database. The exact question and reply are
recorded in explicit-transfer-approval.json. This resolves the preceding
automatic approval block for this frozen selection; the original rejection
remains preserved. No wider or continuously growing workload is included.

The prepared continuation launched at 2026-09-17T03:39:47.203392Z as
PID 1520520, using plan 79a892b67c2ab4f7371ae5fb6a328ec2c10839a643744c367f2a8ce364bb762d.
Readback verified 20 native Luna workers, the 33,576-call durable budget,
five-minute database waits, a three-minute restart delay and the persistent
maximum of ten automatic resumptions. No scheduled job was changed.

At 2026-09-17T03:41:56.830842Z, the locked immutable publication audit verified
19 new outputs and 34055 total.
All 34,036 pre-launch identities and 6,060 earlier terminal records were
preserved. Five exact source/response/output/assessment comparisons and
Luna/high subscription receipts passed, with no structured foreign-key
violations. These startup checks do not claim completion of the remaining
queue or a live automatic-restart recovery.

At 2026-09-17T03:43:12.533785Z, the coordinator remained alive with status
running, 20 in flight, 46 outputs
saved in this continuation, 3 automatic model retries and
0 automatic restarts. The projected finish is roughly
13.5-14 hours after launch (about 1:10-1:40 p.m. Toronto September 17), subject
to throughput, retry delays and store contention.

The validated source pins were unchanged, so the 58 passing focused checks
were reused; no new executable tests were required. Evidence under
.local/transcript-resilience-20260917/: explicit-transfer-approval.json,
started.json, launch-verification.json, initial-publication-audit.json and
handoff-status.json. The current active-run pointer and RUN_STATUS.md identify
this supervisor; backlog.json and BACKLOG.md record the expanded selection.


## September 17, 2026 UTC - one user-requested Sharadar continuation completed

The user explicitly requested one Sharadar fetch. This authorized one start of
the existing service to finish its pending September 16 update window, preserving
the selected membership/mapping, September 7-16 lastupdated bounds, existing
SHARADAR_DIRECT_API resolver, fixed company store and replay-safe publisher.
The invocation retained the 500-request, 256-MiB, 3,600-second and 1,000-request
daily ceilings, one-second spacing and no automatic retries. No timer or unit
was changed; this finite manual start does not authorize further manual starts.

The service started September 16 at 23:42:59 Eastern (September 17 03:42:59 UTC)
and finished at 23:49:46 Eastern with exit 0. It completed the 392 remaining
partitions in 392 GETs, all HTTP 200, receiving 3,100,434 bytes. Together with
the 58 earlier completed partitions, the September 16 checkpoint is now complete
at 450 partitions with no pending request or pending refresh window. The three
existing identity gaps remain. No full-history population was repeated.

The new clock-recovery implementation matched its validated source pins.
Its 38 focused offline checks and the two separately rerun cross-channel
identity/replay checks were reused. This invocation's journal contains no
clock-recovery event, so completion does not claim a live clock-step recovery.

The locked immutable postcheck at 03:51:33 UTC preserved all 363,249 previous
observation identities, 363,513 previous version identities and 1,942 previous
capture identities. Five prior complete version records had unchanged hashes.
The continuation added 53 observations, 295 versions (53 first versions plus
242 revisions) and 392 captures. Final totals were 363,302 observations,
363,808 versions and 2,334 captures. All 1,462 new capture memberships passed
scope, identity/version linkage and availability checks; missing or invalid
heads were zero. All 392 original response bodies matched their retained
lengths and SHA-256 hashes. This was a bounded Sharadar audit, not a full-store
integrity scan.

Evidence: .local/sharadar-manual-refresh-20260917/ contains the one-use
authorization and launch marker, preserved queue/checkpoint and canonical
identity baseline, startup record, completion, postcheck, journal and timer
readback. Production code was unchanged by this operation; no commit or push.


## September 17, 2026 UTC - requested Luna resume after store-availability pause

The user explicitly requested resuming the paused run. Its existing full-text
approval covers the same frozen 11,192-input plan, OpenAI through the Codex
subscription, company publication and a 33,576 aggregate call ceiling.
The remaining 4,095 inputs are all untouched members of that approved plan:
4,093 unrecorded inputs plus two TILE input failures with zero consumed calls.
No completed model attempt or earlier excluded failure is reopened.

The previous run stopped at 11:33:52 UTC after two StoreUnavailableError input
checks. The precise original availability cause was not established; the
supervisor intentionally did not classify this error as an automatically
restartable lock timeout. Current locked immutable checks succeeded, both
frozen TILE sources matched the canonical source, and all 41,133 pre-resume
output identities were saved as a baseline.

Preflight preserved 7,097 completed records, the two original input-failure
records, all existing evidence and the durable counters: 7,255 calls charged,
158 automatic model retries, zero automatic resumptions. The remaining
population can use at most 12,285 further native calls (three per input),
inside the unchanged aggregate cap; counters were not reset. Existing
Luna/high, 20 workers, five-minute lock waits, three-minute delay and ten
automatic-resumption ceiling are unchanged. The StoreUnavailableError stop
classification is unchanged; a future such failure may still require attention.

A private one-use wrapper resumed the existing plan at 2026-09-17T13:24:43.983851Z
as PID 3424162. The current explicit approval and subset proof were
accepted without another confirmation. The wrapper reuses the validated
engine and stores this invocation's log/results separately. Syntax, review,
membership, preserved-counter and runtime checks passed; production source
pins matched the 58-check validated baseline, so no new test run was required.

The initial locked immutable audit at 2026-09-17T13:26:36.279967Z verified
12 new outputs and 41145 total, preservation
of all 41,133 earlier output identities and 7,097 successful record bytes,
five exact source/response/output/assessment comparisons, Luna/high
subscription receipts and no structured foreign-key violations.
At 2026-09-17T13:28:18.355232Z, the coordinator was running with
20 in flight and 45 newly saved outputs.
This is startup evidence, not completion of all remaining inputs or a fix
for every future store-availability failure.

Evidence: .local/transcript-resilience-20260917/resume-after-store-pause/
contains original approval, finite subset authorization, saved checkpoint/
budget/supervisor state, original blocked records, identity baseline,
preflight.json, helper-validation.json, resume.py, started.json,
launch-verification.json, initial-publication-audit.json and handoff-status.json.
The active-run pointer and live summary identify this invocation. No provider
fetch, new population, production change, scheduler change, commit or push.


## September 17, 2026 UTC - Luna resumed with exact timeout-wording correction

The user requested resuming the paused Luna run. The failure snapshot showed
21 terminal model failures: 20 final stream-disconnection failures after three
attempts, and one one-attempt native "request timed out" failure. That exact
message was absent from the transient allowlist and prevented automatic
continuation. A one-line correction recognizes it under the existing timeout
policy. Completed/malformed/tool output and permanent authentication/usage
errors still do not qualify. No request identity, retry limit, supervisor
allowance or model budget was changed.

Twenty-one focused retry and direct-supervisor checks passed in an isolated
source copy with explicit temporary stores and network blocked. New cases
cover recovery, three-attempt exhaustion retaining the transient flag, and a
timeout with a permanent authentication error. Existing retained native
evidence now classifies as transient without any evidence change. The prior
58-check evidence remains applicable to unchanged components; no full-suite
trigger applied.

Preflight selected exactly 2,143 untouched inputs within the same explicitly
approved 11,192-input plan. All 9,049 terminal records, including all 21
failed inputs, remain excluded and byte-preserved. The locked immutable
baseline contained 43,064 structured outputs. At restart, 9,275 calls and
226 model retries had been charged, with zero automatic resumptions. The
remaining population can use at most 6,429 more native calls, within the
unchanged 33,576 aggregate ceiling. No counter was reset and no previously
failed input was reopened.

The continuation launched at 2026-09-17T15:56:03.752048Z as PID
3951213, retaining 20 Luna/high workers, the existing OpenAI Codex
subscription, company publisher, five-minute lock waits, three-minute
backoff and ten automatic-resumption ceiling. The existing explicit transfer
approval and finite-subset proof were accepted without another confirmation.

The locked immutable audit at 2026-09-17T15:57:59.181945Z verified
12 fresh outputs and 43076 total, all 43,064 prior
identities and all 9,049 terminal records unchanged, five exact
source/response/output/assessment comparisons, expected Luna/high subscription
receipts and no structured foreign-key violations. At 2026-09-17T16:00:05.935289Z,
the run remained active with 20 in flight, 46 saved since restart,
0 new model retries and 0 automatic resumptions.
A live automatic-restart recovery is not claimed by this startup audit.

Evidence under .local/transcript-resilience-20260917/resume-after-timeout-pause/
includes exact preimages and production/helper diffs, impact and focused logs,
validation.json, original approval, finite authorization, preserved checkpoints
and record hashes, database baseline, preflight, selected remaining inputs,
resume.py, started.json, launch verification, publication audit and handoff.
Current pointers and backlog summaries identify this invocation. No provider
fetch, new population, scheduler change, commit or push occurred.


## September 18, 2026 UTC - explicit retry of 21 Luna model failures

The user selected the reported 21 held failures and asked, "can you retry this?"
This explicitly authorizes one fresh bounded retry of those exact failed inputs,
superseding their earlier held status for this operation only. The original
11,192-input batch finished with 11,171 saved and 21 failures; its 11,458
charged calls, 266 retry charges and original evidence remain unchanged.

Preparation verified all 21 inputs still lack a structured output, and their
full source text, request bytes and Luna/high configuration match the prior
approved OpenAI-transfer population. The new finite batch permits at most
63 additional calls, three per input, with 20 workers through the existing
Codex ChatGPT subscription. Successful drafts and automatic assessments use
the existing company publisher. Other failed or newly saved inputs are excluded.

The existing retry and supervisor engines are unchanged: 300-second store-lock
waits, 180-second delayed continuation and at most ten automatic continuations.
A separate private batch, paired receipts and native evidence directory preserve
the earlier terminal failures. This does not reset or refund the original budget.

The one-use launcher started at 2026-09-18T02:07:07.245153Z as PID 371952.
The locked immutable baseline contained 45,207 outputs; 11,501 original evidence
files were hashed for preservation. Source pins still match the existing
21-check timeout correction and unchanged 58-check baseline, so these results
were reused. The private helper passed syntax and bounded membership/readiness
checks. No full suite, production change, scheduler change, provider fetch,
extra review, API fallback, commit or push is included.

Evidence: .local/transcript-failed21-retry-20260918/ contains authorization,
the prior full-text transfer approval, original evidence hashes, database
baseline, exact selected entries, frozen plan, validation, preflight, one-use
launcher and started receipt. Its result.json and completion-audit.json record
the final outcome when available. The active-run pointer identifies this batch.


### Completion of the explicit 21-input retry

The retry completed at 2026-09-18T02:10:50.845659Z: all 21 outputs saved,
28 native calls including seven automatic retries, zero terminal failures and
zero automatic continuations. The original expanded population now has all
11,192 outputs; historical failed receipts remain unchanged.

The locked immutable completion audit at 2026-09-18T02:12:28.780977Z
verified all 21 exact source/request/response/output/assessment bindings,
Luna/high subscription receipts for all 28 calls, preservation of all 45,207
previous output identities and all 11,501 protected original evidence files,
and zero foreign-key violations in both structured transcript tables.
The verified total is 45,228 outputs. This verifies faithful publication of
model drafts and automatic assessments, not independent factual approval.
No additional call or population was started.

The private completion receipt and backlog summaries were updated. Production
code and earlier test pins remain unchanged; required operational checks passed.
Evidence: .local/transcript-failed21-retry-20260918/result.json and
completion-audit.json.


## September 18, 2026 - incremental Equibles refresh prepared

The user approved implementing a quota-conscious live transcript refresh with
recent-reporting tickers on weekdays and fallback work on both weekend days.
The [incremental contract](EQUIBLES_INCREMENTAL_REFRESH_2026-09-18.md) records
the fixed 2,222-ticker roster, proposed daily 02:00 America/Toronto schedule,
shared 100-request UTC-day ceiling, finite retry policy, retained-page recovery
and deduplicated private Luna queue. It introduces no automatic model calls.

The implementation and 148 unique component/direct-consumer checks passed,
including the final 35-case refresh run. A separate read-only reviewer passed
ten affected checks and found no remaining blocker. Systemd definition
validation passed. This evidence does not claim a live provider run.

Automatic approval review rejected activation before execution because it
requires explicit confirmation to narrow the shared account cap, create
activation state and install/enable the recurring timer. The concrete request
also includes one initial fetch inside the same 100-call daily cap.
Until that confirmation is received, this new job remains inactive and the
existing account cap is unchanged. No provider requests have been made for
this implementation. Evidence: .local/equibles-refresh-20260918/.

## September 19, 2026 - WSL host scheduling repair

The user explicitly requested fixing the repeated WSL shutdowns and broken host
launch setup. Eight obsolete Windows QuantData tasks referenced a missing legacy
script and returned 127; their exact definitions were backed up before disabling.
One least-privileged, interactive Windows host-wake task now launches only
Ubuntu /usr/bin/true two minutes before the existing collection slots and at logon.
It does not execute a collector. Supported WSL idle-retention settings and the
Ubuntu cloud-init opt-out removed the observed host lifecycle failures.

Independent preactivation review passed after bounded corrections. A quiet,
graceful service shutdown and VM restart reduced observed startup from 124.242
to 3.296 seconds. All 13 existing Linux timer definitions remained byte-identical
and recovered enabled/active/waiting. The actual Windows wake schedule passed
366-day/11,929-slot coverage; a 90.181-second idle observation retained the same boot.
No manual provider call, retry, completed-population repeat, store operation,
collector schedule change, new credential, or exposure was performed.

This is dated host evidence, not proof of future provider outcomes. The task uses
the existing signed-in Windows identity; power-off/signed-out execution and
sleep/resume were not validated. Earlier collector/data-gap failures remain separate.
Details, rollback paths, source hash, and exact evidence are in
[the WSL host repair record](WSL_HOST_REPAIR_2026-09-19.md).


<a id="recorded-primary-fundamentals"></a>

### September 19, 2026 - Sharadar primary and conditional SEC facts

The user's approval to implement the Sharadar-primary recommendation supersedes
the earlier full-pair-every-day SEC refresh policy for future normal weekday
07:15 executions only. The selected roster, existing timer, provider credentials,
4,500-request/12-GiB/six-hour cap and no-retry policy remain in force.
SEC submissions stay current; CompanyFacts is conditional on unseen financial
accessions or a separately authorized finite discrepancy/gap check.
The [focused contract](SHARADAR_PRIMARY_FUNDAMENTALS_2026-09-19.md) defines
consumer versions, trigger semantics, finite targeted checks and retained-state
boundaries. Existing SEC history and old ledgers remain retained.

The existing service entry point loads this policy on its next scheduled run.
Implementation does not manually trigger the timer or fetch any provider data.
This decision changes future behavior and is not evidence that a post-change
live run has already succeeded.


The scoped offline checks and fresh independent verification completed
successfully; validation and the observed cold-read limitation are recorded in
the focused contract. Read-only runtime discovery and AAPL v3 reads succeeded.
The installed SEC timer remained active/enabled, next September 21 at 07:15 EDT.
No post-change scheduled run or manual provider request is claimed.


## September 19, 2026 UTC - selective blocked-extraction rerun prepared; transfer gate

The user approved rerunning genuinely blocked transcript extractions with Luna
and storing the results. A locked immutable check of all 9,105 currently blocked
saved extractions selected 3,807 across 1,598 tickers with structural errors;
5,298 validator-only or ambiguous cases remain deferred, with their statuses
unchanged. The selection includes 3,790 Luna and 17 Terra originals. It excludes
the 62 captures without saved extractions. The scope is a single fresh attempt
per selected transcript, 3,807 calls maximum, no review calls or automatic
retries, using Luna/high and 20 workers through the existing OpenAI Codex
ChatGPT subscription. New response identities bind the original response hash;
the existing publisher appends versions and preserves the original drafts.

Plan `130928215e36af16cedd3eba44b75ef3b81229a1eae4922100df66256425d028`
and exact selection/source hashes are frozen privately under
`.local/transcript-blocked-rerun-20260919/`. Nine focused offline tests passed
after correcting private-directory creation, covering append-only lineage,
zero-call replay, failed-attempt finality, stale selection, publication recovery,
evidence tampering, truthful blocked status, and conservative selection.

Automatic approval review rejected the launch before execution: it acknowledged
rerun and storage approval but required explicit authorization to transfer the
sensitive full-transcript payload to OpenAI through the Codex subscription.
No workaround or indirect launch was attempted. There were zero model calls
and zero new database outputs. A locked immutable postcheck confirmed all
45,228 outputs and 45,243 assessments unchanged. The scoped launch awaits that
explicit payload/destination confirmation. Evidence: `launch-blocked.json`,
`triage-summary.json`, `prepared-plan.json`, `authorization.json`,
`validation.json`, and `RUN_STATUS.md` in the private directory above.


## September 19, 2026 UTC - requested standalone transcript sharing copy prepared

The user requested a dedicated `transcripts.sqlite` copy after the selective
Luna rerun, containing raw transcripts and extracted summaries for sharing with
a friend, while leaving existing infrastructure and workflow unchanged. The
fixed output is `exports/transcripts.sqlite`; it is a non-authoritative, one-time
local artifact, not a registered operational store or public export service.
No external file delivery was requested or attempted.

Private contract `transcripts_share_snapshot.v1` and the tested helper under
`.local/transcripts-share-20260919/` preserve exact raw-page bytes, all extraction
versions and assessment JSON, and add readable/latest-summary views. Source
reads use the physical company lock and pinned immutable reader to form a
consistent transcript-only SQLite snapshot. Rendering occurs from that detached
read-only snapshot. Count and typed-column hashes, foreign-key checks, integrity
checks, and non-overwriting atomic publication gate the final file. This adds no
registry entry, migration, collector, timer, path binding, or application change.

Five focused offline tests passed after a fixture setup correction, with explicit
temporary stores and no network/default-store fallback. The copy authorization,
fixed scope, implementation hash, tests and current status are retained in
`copy-authorization.json`, `validation.json`, and `STATUS.md` in that directory.
No live snapshot or final database was created: the requested sequencing waits
for the preceding rerun, whose full-text transfer remains at the automatic
approval gate described above, or an explicit choice to copy current data.


### September 19, 2026 UTC - sharing-copy selection narrowed to one extraction

The user clarified that each raw transcript should have only one extracted
version in the dedicated sharing database. This supersedes the all-version
selection for that artifact only. Private contract `transcripts_share_snapshot.v2`
selects the newest saved extraction by `available_at`, then `analysis_id`, per
raw `capture_id`, and copies only assessments for that selected extraction.
A unique capture constraint enforces the single-version invariant. Raw transcripts
without extraction remain explicit, and original quality labels are preserved.
The canonical company database retains its original extraction history; nothing
is overwritten or deleted. Six focused offline tests passed, including equal-time
tie-breaking, matching assessments and unchanged source-store fingerprints.
No live snapshot or final file was created, and the separate Luna transfer
approval remains pending. Evidence stays under `.local/transcripts-share-20260919/`.


## September 19, 2026 UTC - full-text transfer confirmed; selective Luna rerun active

In response to the scoped payload/destination request, the user confirmed:
"OK great, now pls send those to luna and extract again". The exact approval is
retained in `.local/transcript-blocked-rerun-20260919/explicit-transfer-approval.json`.
Automatic review permitted the unchanged launch: 3,807 selected full transcripts
to OpenAI via the existing Codex ChatGPT subscription, Luna/high, 20 workers,
3,807 calls maximum, one fresh attempt per input, no automatic retries or reviews.
This supersedes only the earlier transfer hold for this frozen plan.

The coordinator launched at 2026-09-19T22:52:45.655706Z (PID 24301).
Initial model failures are retained without retry. The first five new saved
outputs passed exact source/response/output/assessment comparisons. The locked
immutable audit at 2026-09-19T22:57:42.083207Z verified all 45,228 previous outputs
and 45,243 previous assessments unchanged, with no structured-table foreign-key
violations. The run remains in progress; its authoritative private progress and
result receipts are under `.local/transcript-blocked-rerun-20260919/`.

A one-shot, no-model completion helper (PID 43956) waits for this exact
finite run, verifies all new saved outputs and original preservation, then invokes
the tested latest-only `transcripts.sqlite` exporter. Paused, incomplete or
out-of-scope results stop for attention; the helper adds no model retry, recurring
unit, scheduler or operational path change. Its finalization gate accepted two
valid terminal cases and rejected six incomplete/out-of-scope cases. Export
validation remains six passing temporary-store tests. Current follow-up status:
`.local/transcripts-share-20260919/pipeline-status.json`; export completion
receipt will be `completion.json`. No final sharing database exists yet.


## September 19, 2026 UTC - user-requested increase to 50 Luna workers

The user requested up to 50 Luna workers for the active selective rerun. This
changes runtime concurrency only: the frozen 3,807-transcript selection,
3,807-call cap, Luna/high profile, full-text transfer destination, append-only
publication and zero automatic retries remain unchanged. The scoped approval
is `.local/transcript-blocked-rerun-20260919/concurrency-50-approval.json`.

The original coordinator was signalled with SIGINT on its main thread so its
ThreadPoolExecutor waits for in-flight jobs to finish. Native model child
sessions were not signalled. The idle one-shot export waiter was stopped for
reconnection to the continuation. A bounded one-time transition controller
launched at 2026-09-19T23:10:44.177740Z (PID 74515) waits up to
25 minutes for the existing calls and coordinator to drain. At this readback,
1 native call remained in flight; 186 calls had started in the initial
20-worker segment. No 50-worker launch is claimed yet.

On drain completion the controller verifies every original attempt is terminal,
freezes hashes of its evidence, resumes the unchanged plan with the supported
50-worker override, checks the old evidence remains unchanged, and reconnects
the same latest-only transcript export. It cannot add inputs or retry terminal
failures. Resume guards preserve the original runner and its nine-test pin.
The export finalization gate still rejects incomplete/out-of-scope results;
its coordinator tracking was extended to the new private launcher. Source
compilation and pure completion-gate checks passed.

Live transition evidence is `resize-status.json`; activation will be recorded
in `runtime-started.json` and `resize-preflight.json`, with ongoing progress in
`live-status.json`. The resumed export waiter writes `finalizer-resumed.json`
and `pipeline-status.json` under `.local/transcripts-share-20260919/`. No
recurring unit, infrastructure configuration, model prompt, or canonical schema
was changed.


## September 20, 2026 UTC - selective Luna rerun and sharing copy completed

The 50-worker continuation completed at 2026-09-20T01:02:20.601249Z after exactly
3,807 total native Luna/high subscription calls across both concurrency segments.
It saved 3,735 new output versions and retained 72 terminal model failures with
no retry. Automatic assessment outcomes for the new versions are 578 accepted,
1,955 accepted with flags and 1,202 blocked. The remaining blocked status is
retained truthfully; neither model completion nor storage claims verified facts.

The locked immutable completion audit at 2026-09-20T01:04:07.822695Z verified all
3,735 new source/response/output/assessment relationships, all 45,228 original
output identities and 45,243 original assessments unchanged, and zero structured
foreign-key violations. There are now 48,963 canonical output versions. The
original 20-worker segment evidence was also hash-preserved through continuation.

The one-shot export then completed at 2026-09-20T01:06:48.010848Z.
`exports/transcripts.sqlite` contains 45,290 raw captures, 45,290 exact raw pages,
45,228 latest-only extractions and 45,243 matching assessments. Exactly one
extraction per capture is enforced; 62 captures remain explicitly missing an
extraction. Copy quality totals are 5,653 accepted, 33,003 accepted with flags,
and 6,572 blocked. It is a self-contained SQLite file of 2,886,385,664 bytes,
with no WAL/SHM/journal dependency. Exact copied-column hashes, integrity and
foreign-key checks passed. SHA-256:
`aa40bb10ce874dba0740147c11479b44d4078b2516d9b1b85b5c0dfc9c26c2aa`.

The database can be shared as the requested standalone copy. No file was sent
to another person. Existing store ownership, schema, collectors, paths and
workflow remain unchanged. Completion evidence is retained under
`.local/transcript-blocked-rerun-20260919/completion-audit.json` and
`.local/transcripts-share-20260919/completion.json`; current human-readable
status files and the private run pointer have been updated to completion.

## September 20, 2026 UTC — announcement-based forward P/E research artifact

The user's implementation request authorized a manual retained-data reconstruction
of four-quarter forward EPS and daily forward P/E, rolling on actual earnings
announcements. The method and command are documented in
[the forward P/E research guide](../FORWARD_PE_PROXY.md). It adds two isolated
calculation/builder modules and focused tests; no canonical schema, public-tool
contract, collector, recurring unit or UI behavior changes.

The completed build used cutoff `2026-09-20T03:52:40.119234Z`, processed 2,182
retained quarterly-estimate tickers, and wrote
`exports/forward-pe/20260920T035241048695Z.sqlite` (6,058,237,952 bytes).
It contains 12,636,373 daily rows and 6,428,083 provisional P/E values across
1,936 tickers; 246 tickers have no numeric P/E. All numeric ratios retain the
explicit unresolved currency/share-basis flag. Unknown announcement time rolls
to the next US cash-equity session; unmapped/conflicting periods, missing
quarters/prices and nonpositive EPS remain explicit gaps.

There were zero provider calls, credential reads, canonical writes, migrations,
scheduler actions or external transfers. Source reads used coordinated physical
locks and quiet immutable readers, with a shared build cutoff and per-instrument
coherence. The output is a separate immutable research artifact, not point-in-time
consensus or operational authority.

All 26 focused tests passed; SQLite integrity/foreign-key checks and eight
source-arithmetic spot checks passed. The companion manifest preserves both
implementation hashes; a basename-key collision in the initial embedded hash
map was corrected for future builds without rewriting this completed database.
Private execution source copies and receipt are in
`.local/forward-pe-20260920/`. Earlier three-ticker pilot artifacts and an
interrupted full derivative build remain separate; the interruption removed a
repeated metadata scan and performed no provider retry or source mutation.

<a id="recorded-weekly-price-recovery"></a>

## September 20, 2026 — weekly price repair empty-response recovery

The user's explicit request authorizes fixing per-ticker empty-response
handling and one manual recovery of the failed September 14–18 weekly price
repair. The finite provider scope is ATAI/IRBO for that same week: reuse the
retained ATAI empty response and make at most **one new IRBO FMP full-EOD
request**, bounded to 64 KiB/five rows and the existing 60-minute invocation
deadline, with zero retries. Use the existing `host_fetch(ROOT, "fmp",
environment={})` credential resolver/shared allowance, fixed `data/market.sqlite`,
physical locks and missing-only publisher. Existing prices and original run
receipts are preserved; no alternative symbol, older gap or population is
authorized by this recovery.

The component change records empty arrays as unresolved ticker outcomes and
continues other units. Provider stops and uncertain requests remain fatal.
The new explicit recovery guard checks original request/byte accounting and
uses only its unused attempt allowance. The existing Saturday timer and service
configuration are unchanged; normal future runs use the corrected wrapper.
See [weekly repair](WEEKLY_PRICE_REPAIR_2026-09-15.md#empty-response-isolation-and-explicit-recovery--september-20-2026).
Task preimages and the exact read-only preflight are retained under
`.local/weekly-price-repair-recovery-20260920/`. Completion evidence follows
the bounded manual invocation; this scope entry alone makes no execution claim.

The authorized manual recovery completed with exactly one new IRBO request
(HTTP 200, empty array, two bytes) and reuse of ATAI's saved empty response.
Both tickers were processed; `failure=null`. Zero canonical prices were
published. Ten missing price rows and the existing eight calendar/listing
issues remain honestly flagged as `complete_with_gaps`; no extra request or
retry followed. All eight original immutable evidence files and the scoped
canonical rows were unchanged. Queue totals reconcile to two requests/four
bytes across original run and recovery, with no pending request or false
publication receipt.

The final 31 affected weekly/history tests passed, alongside the earlier 26
adjacent history-window/publication-deadline checks. Independent verification
inspected the 31-test log and eight additional guard probes; its missing-ledger
finding was corrected before execution. Final source hash and both preflight
and execution lineage are recorded in `manual-execution.json` and
`completion.json` under the task evidence directory.


## September 20, 2026 — targeted historical stock-price gaps

The user explicitly requested a targeted fill of historically missing prices
outside the weekly patch. The frozen read-only audit and a fresh locked
immutable preflight identify 82,646 missing dates across 724 equities, all
before September 14, 2026, within each instrument's existing saved-history
boundary. The latest-week ATAI/IRBO empty cases are excluded.

One finite pass groups those exact gaps into 1,446 bounded historical windows,
prioritizing August 17–September 1 gaps. Maximum: 1,446 FMP full-EOD GETs,
512 MiB total response bytes and 3,600 seconds, with zero retries. Use the
existing host_fetch(ROOT, "fmp", environment={}) credential resolver and shared
provider allowance, fixed data/market.sqlite, physical locks, original-response
validation and the existing transactionally missing-only publisher. Previously
saved prices cannot be corrected; any returned new date outside the frozen
target set rejects that response. Empty or invalid responses remain unresolved;
provider stops and uncertain attempts stop the invocation. No recurring unit,
calendar convention, identity, schema or price semantics changes.

Manifest and baseline:
data/.operations/collection/daily-prices/targeted-gap-fill-20260920/.
One-time driver and focused fixture evidence:
.local/historical-gap-fill-20260920/ (22 passing checks: six new response and
preservation cases, six direct price cases, nine historical-window cases and
one empty-response isolation case). This authorization/preflight entry does
not itself claim execution or completeness. A bounded canonical postcheck
will record actual fills, unresolved dates and existing-price preservation.


The pass completed at 15:35:52 UTC with all **1,446 requests**, **79,378,115
response bytes**, no retry and no fatal/provider-level failure. It restored
**6,074 canonical prices across 514 stocks**, comprising the entire observed
August 17–September 1 shared gap, including GOOGL's 12 missing dates. Of the
724 targeted stocks, 341 now have no remaining dates missing in their audited
history. **76,572 older target dates remain**: 75,341 were absent from the
returned provider rows and 1,231 were present in responses rejected by strict
validation. These counts do not assert that each calendar absence represents
a day on which the historical instrument actually traded.

The 1,446 unit outcomes comprise 1,125 validated publications, 62 empty arrays
and 259 invalid responses (250 date-bound/duplicate failures and nine OHLC
failures). A validated publication with zero written versions is not reported
as a repaired gap. Original response bytes remain in the private queue.

The locked immutable postcheck found every baseline current-version identifier
unchanged, no unapproved new date and no pending request. A second bounded
lineage check matched all 6,074 new prices to the exact request scope, original
response hash, capture timestamp and publication receipt; accounting matched
all charged attempts/bytes. The full 2,248-stock audit therefore retains 76,577
calendar absences, including the five excluded latest-week ATAI dates; 2,774
remain in the five-year interval. The weekly schedule and the separate
forward-P/E serving snapshot were not changed by this operation.

Evidence: manifest.json, baseline.json, started.json, result.json,
verification.json, completion.json and queue/ beneath the targeted-gap-fill
root. Human-readable and exact-date results are
exports/price-coverage/20260920/targeted-fill-result.md and
exports/price-coverage/20260920/targeted-fill-result.json. There were no source,
registry/schema, scheduler or canonical reader/publisher changes; the driver
is task-local, and unrelated workspace changes remain preserved.


## September 20 Toronto / September 21 UTC — P/E snapshot price repair

Following the user's report that GOOGL still showed 1,243/1,255 sessions despite
the completed canonical fill, one finite derived-snapshot refresh applied the
6,074 repaired dates across all 514 affected tickers. Canonical access used
the existing physical market lock, quiet immutable reader and capture-validated
close binding; there were zero provider requests and zero canonical writes.
The original snapshot and original estimate cutoff were preserved. Only the
explicitly repaired dates and their source-price lineage were updated in a
new artifact; full comparisons verified unchanged estimates, windows and all
other daily rows. No timer, service, recurring automation or core source changed.

Artifact: `exports/forward-pe/20260921T012728147712Z.sqlite`. The dashboard's
current.json pointer was atomically switched only after integrity, foreign-key,
source/calculation and full snapshot comparisons passed. GOOGL's actual HTTP
page and derived reader returned **1,255/1,255**, as did AAPL and NVDA. Three
isolated merge tests and 31 calculation/dashboard regressions passed. Browser
automation could not initialize its Windows sandbox; no browser automation
success is claimed. Evidence: `.local/forward-pe-price-refresh-20260920/` and
the new artifact's adjacent manifest. The manual price-only run took 420.956
seconds, within its 900-second bound; the daily P/E schedule remains inactive.


## September 20 Toronto / September 21 UTC — forward P/E quarter matching

The user explicitly approved fixing the quarter matcher after the MU diagnosis.
The local research calculation now prioritizes a unique exact positive
reported-revenue match to a statement period over inconsistent transcript
fiscal labels, preserving a visible warning and all existing explicit-period,
ambiguity, 120-day, announcement-timing, cutoff and nonpositive-EPS rules.
This changes the derived research calculation only; no shared identity, source
time/missingness, schema, provider or canonical publication rule changes.

One finite snapshot revision reprocessed 40 affected tickers (335,883 daily
rows) from the already saved immutable inputs. Source inputs were verified
byte-identical and all prices unchanged. Zero provider requests, canonical
reads or canonical writes occurred. The original artifacts remain available.
Artifact `exports/forward-pe/20260921T015229883870Z.sqlite` passed full integrity
and foreign-key checks before current.json was atomically switched. MU now
serves 943/1,255 five-year P/E observations, with 312 nonpositive-EPS sessions;
GOOGL retains 1,255/1,255. Both were verified in actual HTTP responses and the
bounded reader. Thirty-six focused calculation/dashboard checks passed.

The initial unpublished bulk replacement was interrupted after detecting
repeated full-table foreign-key scans. Its completed recomputation and staging
copy were reused with final mandatory constraint validation; the successful
continuation ran 455.941 seconds under its 600-second cap. Original failure
output is retained. WSL command startup also timed out transiently; direct
Linux-runtime checks verified the job continued and completed. No host service
was restarted, and no browser automation success is claimed. Evidence and
preimages: `.local/forward-pe-quarter-match-20260920/`; completion is also in
the new artifact's adjacent manifest. There was no commit, push or recurring
activation in this scope.


## September 20 Toronto / September 21 UTC — signed forward P/E

The user approved updating the research calculation and dashboard to display
negative P/E as Expected loss, distinguish zero EPS from missing inputs, and
break the P/E chart whenever forward EPS changes sign. This supersedes the
previous derived nonpositive-EPS display rule; other data/basis blockers stay
in force. No shared canonical semantics or schemas change.

One finite saved-input revision changed 12,357 windows and 748,105 daily rows
across 946 affected tickers. It adds 746,102 negative ratios, with 1,840 zero-EPS
sessions separately undefined. Prices, source inputs, positive ratios and
cutoffs are preserved. There were zero provider requests, canonical reads or
canonical writes. The artifact is
`exports/forward-pe/20260921T033230486298Z.sqlite`; current.json was atomically
selected only after full integrity and foreign-key checks passed. Earlier
artifacts remain immutable. Unpublished validation failures are retained in
the receipt directory; final validation reused the completed calculation.

MU's actual HTTP page and Chromium browser now show 1,255/1,255 five-year
ratios, including 312 negative. GOOGL, AAPL and NVDA retain full five-year
coverage. Thirty-nine focused tests and desktop/mobile/no-JavaScript browser
checks passed; screenshots were visually reviewed. The existing manually
launched Inspector on port 8766 was restarted to load code and cached assets.
No recurring unit was changed or activated; no commit or push was performed.
Evidence: `.local/forward-pe-signed-20260920/` and the artifact's adjacent manifest.


## September 21, 2026 — all-ticker forward-P/E quarter reconciliation

The user approved the audit recommendation to handle fiscal-label aliases,
corroborated announcement/quarter disagreements, estimate-date revisions and
observed long retail quarters in the derived research calculation. This permits
one finite revision of all 2,182 retained ticker inputs and selection of its
validated serving artifact. It adds no provider workload, canonical mutation,
new daily schedule or change to shared source identity/time semantics.

Artifact `exports/forward-pe/20260921T041832814628Z.sqlite` reuses byte-identical
saved inputs and unchanged prices/cutoffs from its signed-P/E parent. The
refresh preserves all 12,636,373 daily rows and every previously numeric ratio,
adding 54,438 ratios across 169 tickers over all history (22,123 across 128
tickers in the five-year view). C and ADBE now serve 1,255/1,255 five-year
ratios; MU retains its 312 negative ratios. Unsupported evidence remains
flagged: the five-year universe still has 289,490 unavailable/undefined ratios.

The one-time computation completed in 809.849 seconds under 1,800 seconds and
the separate validation in 111.962 seconds under 900 seconds. Full SQLite
integrity/foreign-key checks, saved-input byte equality, metadata/source-code
hashes, targeted arithmetic and counts passed before the atomic pointer switch.
Fifty focused tests passed; Chromium verified the live C, ADBE, MU, GOOGL and KR
pages, selected-day components and provenance, negative ratios and mobile
overflow, with no JavaScript errors. Screenshots were visually reviewed.

There were zero provider requests, canonical reads/writes, service restarts or
recurring-unit actions. A transient WSL connection timeout affected a status
read only; the existing completion log and later Linux browser check confirmed
normal completion. No commit or push was performed. Prior artifacts remain
immutable. Evidence: `.local/forward-pe-link-fix-20260921/`, the adjacent artifact
manifest, and the detailed receipt in `docs/FORWARD_PE_PROXY.md`.


<a id="recorded-derived-refresh"></a>

## September 21, 2026 — daily derived research refresh approved

The user approved implementing the inspected daily derived-data plan: one
retained-input runner at 23:30 weekdays and 06:30 daily, America/Toronto,
plus preserving additions to the existing Windows host-wake schedule two
minutes before those slots. Forward EPS, quarter matching, P/E, and derived
coverage/freshness are the initial scope. Existing provider schedules, source
stores and the transcript sharing copy remain outside mutation scope.

The concrete finite initial run and recurring limits, compact overlay,
per-ticker missingness, historical denominator preservation and runner-owned
copy retention are specified in docs/DERIVED_REFRESH_PLAN.md. The workload
makes zero provider requests and no canonical writes. New service installation
and activation follow focused tests and independent boundary verification.
This entry records approval and implementation scope, not live completion.


### Observed activation and bounded first-run completion

On September 21 the two new derived unit files were installed; systemd observed
the timer enabled/active with next execution at 23:30 EDT September 21, then
06:30 EDT September 22. Windows QuantData-WSLHost now has 16 triggers. Its
14 pre-existing triggers, action, principals and settings were verified
unchanged after adding the two approved wake slots. No collector timer or
provider workload changed.

The single initial local run checked all 2,182 members in 799.218 seconds and
completed September 21 at 12:27:14 Toronto. It selected compact artifact
exports/forward-pe/20260921T161355001773Z.sqlite (1,818,624 bytes), pinned to the
unchanged historical artifact. No new sessions or changed price versions were
found through the September 18 completed session. It retains 12,636,373 daily
rows and 7,243,982 numeric ratios: 217 ticker cohorts were current and 1,965
flagged older/unchecked estimate or earnings captures; no cohort was unreadable.
There were zero provider requests and canonical writes.

The detailed publication receipt, quick-check, foreign-key check and targeted
merged-reader coverage checks passed. The generic activity summary initially
warned because exit 0 was incompatible with partial counts. Its original exit
receipt is preserved; the new runner now returns 2 for complete_with_gaps so
future summaries satisfy the established recorder contract. No shared receipt
validator changed and no second population run was performed. Independent
verification cleared this correction and the missing Status description found
during live browser acceptance.

Component validation: 112 tests, then 51/64 affected checks after the two live
integration corrections (114 distinct checks overall), plus independent
boundary verification. Chromium verified C/MU coverage, negative P/E, selected
days, the new Status section and mobile layout. The manual Inspector was
restarted; no recurring collector was restarted. No commit/push occurred.
Complete receipts, task preimages, tests, screenshots and host task XML are in
.local/derived-refresh-implementation-20260921/. The detailed implementation and
limitations are in docs/DERIVED_REFRESH_PLAN.md.

## September 22, 2026 - selected company short descriptions completed

The user authorized populating a short description for all selected tickers,
reusing existing information and fetching missing profiles from FMP. The finite
scope was the existing 2,248-security major_index_liquid selection and at most
516 single-attempt profile requests through the existing FMP credential resolver,
account allowance, bounded transport and physical-store publication locks.
No recurring unit was changed or manually executed.

The reviewed additive company migration 0019 is applied at registry 2.91.0.
Its full foreign-key gate and prior-schema/ledger/source-count preservation
checks passed. WSL temporarily became unresponsive to new commands during the
migration; it recovered and the original command completed. No WSL restart or
migration repeat was performed by this task.

At 2026-09-22T19:11:00.643053Z, one publication saved all 2,248 short descriptions:
1,732 reused retained FMP text and 516 used newly acquired profiles. All 516
requests returned HTTP 200; total response bytes were 1,386,226, with no retries.
Every description is an extractive source summary, capped at 400 characters;
seven carry an explicit truncation flag. Full descriptions and original raw
source profiles remain retained separately. There are zero coverage gaps and
no industry-only fallback rows.

The immutable completion audit verified every selected identity, source byte
digest and saved description, and both new tables' foreign keys. Full-batch
semantic replay returned unchanged with zero writes and unchanged scoped state.
This completed population must not be repeated without a new finite request.
There is no new scheduler, public tool, UI, export, hosting, or provider family.
See [the contract and completion evidence](COMPANY_SHORT_DESCRIPTIONS_2026-09-22.md)
and .local/company-short-descriptions-20260922/completion-receipt.json.


## September 22, 2026 — Q/XOM identity correction and derived-status presentation

The user explicitly requested mapping Q to Qnity Electronics, Inc. and XOM to
ExxonMobil Holdings Corporation, correcting the derived-calculation UI labels,
and explaining why announcements select the estimated-EPS quarter window.
The finite identity operation corrects exactly two members of the existing
2,248-member FMP mapping: Q to CIK 0002058873 and XOM to CIK 0002115436.
Use retained Q FMP profile bytes, the retained SEC directory, existing canonical
issuers and an explicitly labelled user-reviewed XOM issuer association. Preserve
FMP's predecessor XOM CIK 0000034088 in its original evidence. Append through
the existing mapping publisher; preserve all other mapping rows, instruments,
source data and historical versions. Update only the existing selected-company
activation population's mapping references so its guard accepts this correction.
Maximum provider requests: zero; no company-store writes, new migration, timer
change, collector invocation, retry, or historical population repeat.

The UI uses a read-only projection of completed derived-publication receipts and
identity/time-bound successful source-check receipts, including unchanged checks.
Original run counts, capture dates and ratios remain intact. The P/E formula and
its announcement-based selection of the next four unreported quarters are unchanged.
Task evidence: .local/q-xom-derived-status-20260922/. Completion follows below.

## September 22, 2026 — Sharadar timed retry policy approved

The user approved two recovery attempts for known server failures, at 10 and
30 minutes after the initial failure, then recovery on the next scheduled day,
plus one bounded catch-up of the September 22 failed batch. The existing request,
byte, duration and daily limits still apply. No other recurring unit or provider
is authorized to retry by this decision.

The [Sharadar recovery contract](SHARADAR_DIRECT_MIGRATION_2026-09-12.md#september-22-2026--bounded-sharadar-server-error-recovery)
owns the exact evidence-preserving implementation and validation scope. At this
entry, local validation is in progress; no provider recovery has been executed.

## September 22, 2026 — Q/XOM correction and derived UI completion

The finite correction published at 2026-09-23T02:48:58Z (September 22 Toronto):
collection_mapping_f81aa1375470ce8076e63d0cc75eede3 supersedes
collection_mapping_024203001061dddb7fe0db9e0c01aa92. Exactly Q/XOM changed;
2,246 members and their existing instrument identities are preserved. Both now
resolve through the established selected FMP subject reader to their existing
canonical issuers. Scoped mapping foreign keys passed and semantic replay
returned unchanged/zero writes with identical market database/sidecar file
fingerprints. Original activation bytes are retained; its four population
mapping references alone now select the new mapping. Provider requests and
company-store writes were zero; no collector or population was rerun.

The UI projection and presentation changes are implemented. The manual local
Inspector alone was reloaded; recurring units were untouched. Its stale startup
version check and one assertion were aligned to the already existing registry
2.91.0 so the corrected UI can load. No registry or migration changed in this task.
The unrelated frozen predecessor test in tests.tool_platform.test_forward_pe_tools
still compares the pre-existing 2.91.0 short-description migration against 2.90.0;
its failure is preserved separately and not repaired by this task.


<a id="recorded-price-refresh"></a>

## September 23, 2026 — daily-price mapping guard repair and one catch-up authorized

The user explicitly requested fixing the daily-price startup conflict and one
September 23 catch-up. The scheduled 18:00 run stopped before acquisition:
the Q/XOM issuer correction changed the FMP mapping snapshot ID, although all
2,248 equity price identities and 111 retained ETF/index entries were unchanged.

The price-only compatibility check reconstructs the approved selection at the
original activation cutoff, validates both pinned selections, and permits only
CIK and explanatory-note differences. Membership, price symbols, instrument
identities, provider subjects, resolution status and retained universe snapshots
must remain identical. Original activation, history, quarantine and audit bytes
and their hash checks remain authoritative and unchanged. Shared mapping and
publisher contracts are unchanged.

The one manual run targets session September 23, the existing September 17–23
seven-day window, at most 2,357 FMP requests after the reviewed ATAI/IRBO
exclusions, 160 MiB and 1,755 seconds, with zero retries. It uses the existing
project FMP credential resolver, fixed daily-price queue and replay-safe market
publisher into data/market.sqlite. No timer cadence change, provider substitution
or additional population is authorized. Evidence is retained under
.local/daily-price-gate-20260923/.

Execution evidence: after the derived job finished, the approved WSL restart
restored Linux command execution. All 30 focused offline tests passed in
31.140 seconds, and the live read-only preflight passed with all original
approval hashes unchanged. The existing market-close service was started once
at 2026-09-23 23:52:19 EDT for the September 23 session. Run receipt 5c4ce28f06844fd9897c2be38faf769f
records this separate invocation. AAPL returned HTTP 200 with September 17,
18, 21, 22 and 23 prices and a successful canonical publication. The 2,357-unit
plan is running; this is launch evidence, not a claim of whole-run completion.
See launch-evidence.json, preflight.json and tests.log in the evidence directory.
The original failed 18:00 run remains historical evidence.

## September 22, 2026 — inactive prices and XLE/XLK repair authorized

The user approved stopping future daily and weekly price requests for ATAI and
IRBO while preserving history, and fixing XLE/XLK options with one fresh
September 22 New York session acquisition. Scope: at most 50 Alpaca requests,
64 MiB, 300 seconds, zero retries; fixed paper/indicative feed and existing
project credential resolver. One shared OPRA calendar and fixed ETF underlying
snapshot precede only XLE/XLK catalogs and their existing ten-DTE expiry grid.
Retain original responses before parsing for offline correction without more
GETs. Canonical publication is limited to XLE/XLK through the existing locked,
replay-safe publisher into data/market.sqlite after focused checks. No other
option cohort, timer action, schema change, proxy substitution or FMP
population is authorized. ETF proxy research is read-only. Evidence lives
under .local/market-close-repair-20260922/. This entry asserts authorization,
not successful acquisition or publication.


Q/XOM and derived UI validation evidence is retained under
.local/q-xom-derived-status-20260922/: 174 focused tests passed; one unrelated
registry-predecessor assertion remains failing. Actual GOOGL/Q HTML pages and
mobile layouts passed. Browser testing identified and corrected a cold P/E
reader deadline regression by moving source-check projection to the HTML layer,
pinned to the returned immutable snapshot hash. Nonselected calendar days use
publication receipts without re-indexing source checks for the entire month.
The existing dataset-summary query remains slow (35.302 seconds in the isolated
read before its deadline); this path was not modified. Provider requests remain
zero and no canonical P/E value or formula changed.

Final actual Status browser acceptance passed at HTTP 200 in 47.955 seconds:
2,182 calculated / 2,180 current checks / two input warnings, with original
counts preserved. Calendar wording, keyboard details and 390px mobile layout
passed. The existing stored-data summary displayed its deadline/unavailable
warning; its latency remains an explicitly recorded limitation. Final evidence:
.local/q-xom-derived-status-20260922/browser/status-final-result.json.


<a id="recorded-sharadar-recovery"></a>

## September 22, 2026 — Sharadar recovery implemented and catch-up verified

The approved Sharadar-only server-error policy is implemented: recovery at
10 and 30 minutes after the initial failure, then the next scheduled day,
within the existing request, byte, duration and daily limits. The
[recovery contract and completion evidence](SHARADAR_DIRECT_MIGRATION_2026-09-12.md#september-22-implementation-validation-and-catch-up-completion)
record its exact scope and validation. Ninety focused tests and independent
review passed before the one authorized live invocation.

The catch-up completed September 22 at 23:21:02 EDT (exit 0): 447 additional
requests, all HTTP 200, completed the remaining 447 of 450 partitions for the
fixed September 22 window. It consumed 1,529,390 bytes and 494.709 seconds.
The first recovery succeeded; no second retry or additional manual invocation
was needed. The immutable postcheck verified all new response hashes and
publication links, preserved prior identities and the original failure evidence.
Receipt: .local/sharadar-retry-20260922/postcheck.json; saved run
4f26afa4ce6f4550b8edd5423660b9b9.

The existing timer remains active with its next run September 23 at 05:00 EDT.
No unit configuration, timer cadence, other provider retry policy, credential,
schema or database path changed. This completion consumes the one approved
manual catch-up and grants no additional manual invocation.

## 2026-09-23 — reviewed reporting-period correction for 207 derived symbols

The user's September 23 request explicitly authorized manually resolving the
remaining uncertain period associations and applying the reviewed 207-symbol
cohort in production so forward estimates can be selected. One bounded
retained-input repair published 20260923T042427803995Z.sqlite for September 22:
207 reviewed latest-session rows (206 replaced, one missing AGM row added),
186 numeric research P/E values and 21 explicit input/calendar/release gaps.
The existing 900-second operation bound and 512 MiB export bound were honored.
There were zero provider requests, zero canonical writes, no timer/unit changes
or manual recurring-unit execution, and no earlier/unselected daily-row or
existing-price changes. The previous publication's bytes were verified intact.
The fixed catalog has 208 event-specific decisions for 207 symbols, including
Abivax's newer H1 event; future normal clock execution uses those exact-event
reviews, with inferred-period flags and existing estimate/basis safeguards.

Contract: [forward P/E research proxy](../FORWARD_PE_PROXY.md#reviewed-reporting-periods--september-23-2026).
Receipt and validation: [production report](../../.local/announcement-quarter-production-20260923/production-report.md),
[publication receipt](../../.local/announcement-quarter-production-20260923/publication.json),
[post-publication verification](../../.local/announcement-quarter-production-20260923/production-verification.json).
This records the finite completed user-authorized repair; it grants no new
provider workload or repeat population.


## September 22, 2026 — XLE/XLK repair completed

Completion recorded September 23 against the earlier
[inactive-price and XLE/XLK authorization](#september-22-2026--inactive-prices-and-xlexlk-repair-authorized).
The single acquisition ran September 22 at 23:12:29–23:12:36 EDT
(September 23 at 03:12:29–03:12:36 UTC), using 21 requests, zero retries and
2,242,190 bytes. Original responses were retained before parsing.
Publication completed September 22 at 23:34:39 EDT after independent review
and a transient WSL command-launch delay, without further provider requests.

The existing publisher added 17 captures (nine XLE and eight XLK), 1,158 present
indicative quotes and 124 new contracts, covering all ten configured DTE
targets for each ETF. The reproduced failure was a catalog/chain mismatch:
six XLE and 38 XLK December 18 snapshots contained stale observations from
2025 for contracts absent from the active catalog. The corrected normalizer
excluded those snapshots under the strict
[stale-chain policy](SCHEDULING_AND_LOCKING.md#authorized-in-place-alpaca-etf-option-surface-refresh).
Original raw responses and excluded-symbol metadata remain auditable.
Capture timestamps retain the actual late-evening acquisition time.

The locked immutable postcheck verified preservation of prior capture headers,
unchanged daily-price row count, all 68 raw-response memberships and hashes,
and the option-table foreign keys. Exact replay of all 17 parsed inputs
returned unchanged with zero writes.

Validation evidence:

- [Initial focused suites](../../.local/market-close-repair-20260922/tests.log):
  86 tests passed.
- [Corrected parser suites](../../.local/market-close-repair-20260922/review-correction-tests.log):
  26 tests passed.
- [Option reader suite](../../.local/market-close-repair-20260922/options-reader-tests.log):
  nine tests passed. These suite counts overlap.
- Fresh independent review passed the strict stale-exclusion boundary,
  reader-size guard and locked immutable publication preflight. All 17 retained
  scopes passed the actual reader; the 15 captures previously accepted by the
  original parser retained identical semantic identities, scopes and raw responses.

Receipts:
[acquisition](../../.local/market-close-repair-20260922/options-acquisition/result.json),
[offline replay](../../.local/market-close-repair-20260922/offline-replay.json),
[publication](../../.local/market-close-repair-20260922/publication-progress.json),
[completion and postchecks](../../.local/market-close-repair-20260922/completion.json).

The daily and weekly price wrappers now apply the reviewed ATAI/IRBO
acquisition exclusions while preserving existing history. EWA, EWQ, DAX, EWC,
EWY and EWT remain researched proxy candidates; FMP availability and activation
are pending. No timer or schema changed. This completed acquisition grants
no further manual provider invocation.

## Retained-news read repair — September 23, 2026

The user requested repair and real read-only verification of recent ticker
news search and source status during ordinary collection. The bounded operation
applied only news migration 0010 (one existing capture-metadata lookup index)
through the established locked migration runner and activated exact registry
2.92.0. No provider request, population, scheduler action, retry service or
consumer-project change was performed. Existing facts, prior migration entries
and schema objects were preserved; the canonical foreign-key gate passed.

Public news.search@2.3.0 and news.get_source_status@1.0.0 retain their schemas.
The independent focused gate passed 36 tests and 192 semantic comparisons;
real public AAPL reads, complete as-of pagination and all eight source statuses
passed. This does not authorize another migration, provider fetch or recurring
unit change. Detailed hashes, timing, scope and limitations:
[retained-news repair receipt](RETAINED_NEWS_READ_REPAIR_2026-09-23.md).


## September 24, 2026 — Theta historical-options discovery

The user requested Theta API access verification using the configured project
credential, necessary WSL setup, a dedicated options-store proposal and an
implementation plan. An isolated Python 3.12 environment with the official
thetadata 1.0.11 SDK was installed; no terminal, Java or service was needed.
One authentication and ten bounded AAPL/SPY catalogue/EOD/open-interest/Greeks
requests succeeded, with zero retries and 397,479 serialized response bytes.
The announced bounds were ten data requests, five minutes and 20 MiB.
Private probe evidence and dependency pins are retained under
.local/theta-discovery-20260924/; no credential value or session token was
retained. The returned numeric options-tier code was 2; its named tier and
earliest permitted historical-price date were not verified.

No canonical database was opened, and no options.sqlite, migration, recurring
unit or backfill was created or started. The
[storage and execution proposal](THETA_OPTIONS_STORAGE_AND_EXECUTION_PLAN_2026-09-24.md)
records the requested fifth-store direction, existing Alpaca ownership,
daily-first assumption and concrete remaining gates. Its proposed pilot and
full backfill are not executed or newly authorized by this receipt.

## September 24, 2026  - SPY compact Theta implementation authorization

A later explicit user instruction authorized implementing the proposed compact
options design, fetching SPY first, and discarding unneeded newly acquired source
detail after calculating and publishing the retained statistics. This supersedes
the discovery-only/planning limits above for this exact scope; it does not
change existing Alpaca retention or authorize another ticker or recurring unit.

The declared finite allocation is SPY only: 20,000 data requests including
preflights, calendars and fallbacks; 128 GiB received; 24 hours from the first
current preflight; zero automatic retries. The requested outer range began
2012-06-01; that price probe was denied. The first verified accessible price
sample is 2016-01-04, not proof of the exact subscription floor. The frozen
initial manifest covers 2,696 calendar-verified sessions from 2016-01-04 through
2026-09-23, newest first. The SPY database cap is 15 GiB with a 20 GiB free-space
reserve. No terminal, purchase, subscription change or recurring unit is added.

ADRs and operational details: [ADR 0013](../adr/0013-optional-options-store-and-compact-retention.md)
and [SPY implementation receipt](THETA_SPY_IMPLEMENTATION_2026-09-24.md).
The optional registry owns only `data/options.sqlite`. Publication is atomic,
read back immutably, then exact newly acquired source files are removed with a
hash-checked recovery journal. Selected details, summaries, lineage and receipts
remain; complete source replay is deliberately unavailable. Publication launch
and subsequent coverage are recorded in the receipt and durable run status.

Activation evidence: native WSL manual PID 137553 launched the fixed SPY
entry point. Its first immutable-read check found five published sessions,
SQLite quick_check ok, zero foreign-key violations and verified first-day
source cleanup. This is a running partial history, not a completion receipt.
The durable status and implementation receipt above hold current counts.

## September 24, 2026 - SPY parallel-download upgrade

The user explicitly authorized improving the active SPY backfill with parallel
downloads. This permits replacing the manual sequential process with up to four
concurrent requests, retaining one database writer and the original frozen
SPY manifest, 20,000-request/128-GiB/24-hour global allocation and zero retries.
The authorization clock remains 2026-09-24T14:45:56.548243+00:00.

The sequential process stopped at a verified, fully cached publication boundary
at 18:07:59 UTC: 1,122 sessions committed and 2022-04-01 inputs retained, with
no provider request in flight. The fixed cached day must not be refetched.
The [implementation receipt](THETA_SPY_IMPLEMENTATION_2026-09-24.md) records
focused checks, independent review and subsequent launch/performance evidence.
This is a finite manual continuation, not a recurring-unit change.

Parallel activation: PID 155252 launched at 18:19:19 UTC after independent
verification (35 focused tests passed). Live immutable checks passed; the cached
handover date was published without a new request and no duplicate attempt IDs
were found. At 2026-09-24T18:21:22.631578+00:00, 1144 of 2,696 sessions
were saved with no recorded gaps. The first 22
parallel-session sample averaged 5.47 seconds/day
versus 10.83 in the recent sequential sample.
This dated evidence is not a full-history completion claim.

## September 24, 2026 - SPY Theta history completed; no-repeat scope

The finite manual job completed at 2026-09-24T20:00:18.700484+00:00. All 2,696 planned SPY
sessions from 2016-01-04 through 2026-09-23 are saved with no session gaps.
There are 2,444 sessions with IV/Greeks and 252 price-only sessions (2016).
The main options store and completion backup passed immutable health checks,
with zero foreign-key violations. Requests total 5,659, with zero retries and
no duplicate attempt IDs. Staged source files and pending cleanup are empty.
No recurring unit exists for this collector.

This completed SPY population is on the no-repeat list: do not repeat or broaden
it without a new explicit request naming the finite population/scope. Missing
historical Greeks do not authorize retry. The [implementation receipt](THETA_SPY_IMPLEMENTATION_2026-09-24.md)
and .local/theta-spy-20260924/completion-verification.json retain final evidence.

## September 24, 2026 - authorized Tier-1 ETF expansion

The user explicitly requested extending the completed SPY options history to
other major ETFs with concurrent workers. The prior Tier-1 plan defines the
14 additional roots: QQQ, IWM, DIA, XLB, XLC, XLE, XLF, XLI, XLK, XLP, XLRE,
XLU, XLV, XLY. New finite scope: 37,125 ETF/session units, 2016-01-04 through
2026-09-23, with XLC beginning at its 2018-06-19 listing. SPY stays on the
no-repeat list and is excluded from the new collector's request scope.

The declared ceiling is 120,000 requests, 256 GiB received and 72 hours from
the new run start, zero retries, four downloads globally and one writer.
The options database cap is 25 GiB with 20 GiB free-space reserve. This is a
new finite allocation for different symbols, not an extension of the expired
SPY allocation. No recurring unit, purchase or subscription change is introduced.

The authorized implementation adds options migration 0002 and the fixed ETF
collector under companion registry 1.1.0. SPY facts and its existing backup are
preserved; no original migration bytes change. Expected historical missing
references remain explicit gaps, not invented data. Independent domain review
and migration/first-publication checks precede activation. See the
[ETF expansion receipt](THETA_ETF_EXPANSION_2026-09-24.md) for actual evidence.

ETF expansion activation 2026-09-24T23:06:07.806601+00:00: independent review passed
(44 tests, no skips); options migration 0002 applied with all retained SPY fact
hashes unchanged against both the pre-migration store and existing backup.
Quick check passed and foreign-key violations were zero. The finite 14-ETF
operation started as PID 329558 with the exact allocation above.
See THETA_ETF_EXPANSION_2026-09-24.md and private migration/launch receipts.
This is dated activation evidence, not a claim of current process health.

First live verification 2026-09-24T23:07:10.801782+00:00: all 14 new roots saved for
2026-09-23; 34 ETF/session units complete, zero gaps, four overlapping
requests, zero repeats, zero SPY requests, completed source caches removed.
The completed SPY population remains 2,696 sessions. This is a dated snapshot.

<a id="recorded-equibles-incremental"></a>

## September 25, 2026 - Equibles incremental refresh activated

The user explicitly requested enabling the prepared Equibles refresh, resolving
the September 18 activation-confirmation hold for this scope. The existing
2,222-ticker roster, two provider endpoints, credential resolver, company store
and replay-safe publisher are reused. The shared operating cap is now 100
requests per UTC day; all historical charges and completed histories remain
unchanged. No additional provider allocation or manual initial run was added.

At 2026-09-25T13:06:28Z, the new quant-data-equibles-refresh user timer was
enabled and active/waiting. The schedule is daily 02:00 America/Toronto,
Persistent=true, with first scheduled execution September 26 at 02:00 EDT.
The first-slot admission boundary is September 25 at 02:00 EDT. The completed
historical-backfill timer remains unchanged. Activation made zero provider
requests and no canonical writes.

The locked immutable preflight found 45,290 saved transcripts. Eight original
core/collector-test/unit hashes matched the prior independent review.
Current validation produced 158 actual passing cases; an extra nonexistent
module in the first invocation caused a retained loader error, and the correct
10-case status module subsequently passed. Systemd definition verification and
fresh independent activation review passed. Actual postcheck confirmed the
sole account change to operating_daily_cap=100, unchanged historical usage and
roster, an empty new request ledger, and enabled/waiting timer state.

This is activation evidence, not a successful provider-run claim. Details:
[EQUIBLES_INCREMENTAL_REFRESH_2026-09-18.md](EQUIBLES_INCREMENTAL_REFRESH_2026-09-18.md).
Private receipts: .local/equibles-refresh-activation-20260925/.

## September 25, 2026 - ETF retry/recovery authorization

The user explicitly approved one retry of failed/missing ETF requests, logging
and continuing after exhaustion, and one final missing-data pass after the main
pass. This supersedes the prior zero-retry policy for this expansion only.
Same 14 roots, dates, 37,125 units, store, existing credential resolver and
120,000-request / 256-GiB / original 72-hour allocation. Maximum per endpoint:
two main attempts including any existing attempt, plus one final gap attempt.
All attempts count; no restart resets. Successful responses and completed ETF
and SPY captures are excluded from provider replay. Original manifest evidence
is preserved, with a frozen retry-policy amendment and append-only gap events.
See THETA_ETF_EXPANSION_2026-09-24.md for exact classifications and validation.

Retry recovery cannot infer zero bytes for an interrupted request without its
terminal receipt. Per-request receipt/attempt mismatches stop before auth; the
recorded two UNAVAILABLE requests have complete receipts. All 57 focused tests
passed after this independent-review correction.

Retry activation 2026-09-25T13:18:27.470876+00:00: PID 416018 resumed the exact remaining
manifest after 57 tests and independent verification passed. Existing capture
identities and the two successful caches were recorded; cumulative requests,
bytes, and the original 72-hour clock were not reset. See retry preflight,
verification and launch evidence in .local/theta-etf-20260924/.

Retry live evidence 2026-09-25T13:19:48.350860+00:00: both original failed requests recovered
on one retry each, successful caches reused and cleaned, prior capture identities
unchanged, no SPY requests. 10556 ETF sessions saved, zero outstanding
gaps, PID 416018 running. See dated retry-live-verification receipt.

## September 25, 2026 - finite ETF expansion completed with gaps

Main and final gap passes completed 2026-09-25T21:09:36.923331+00:00. All 37,125 planned
units processed; 35,167 saved, 1,958 unresolved. Completed populations are now
no-repeat; unresolved units exhausted the authorized recovery pass. Additional
provider attempts require a new explicit finite scope. Main/backup populations
match, job health is ok, 82,417 attempts match receipts and bounded phase counts.
SPY remains 2,696 sessions with zero new requests. See expansion receipt and
.local/theta-etf-20260924/completion-verification.json for dated evidence.


## September 26, 2026 — options monitoring dashboard

The user requested a private Sites dashboard with the collector on the existing WSL host, ThetaData Standard, aggregate-only retention, and 5/10/15/30-minute priority tiers. Dashboard inbox and opt-in browser notifications are selected. This is a separate current-day monitoring unit, not a retry of completed historical populations.

The frozen scope is 2,251 equities plus 15 ETFs. Each scheduled five-minute unit has caps of 2,500 provider requests, 256 MiB, 240 seconds, three workers, and zero hidden retries; session caps are 60,000 requests and 8 GiB. The unit uses its own derived monitor materialization and private outbound Site publication. Activation and validation evidence are recorded in [the monitor contract](OPTIONS_MONITOR_2026-09-26.md). No activation or successful provider-run claim is made by this decision record alone.

Options Monitor activation evidence, 2026-09-26: private Site deployment succeeded
at 17:09:53 UTC; frozen roster 2,266 symbols was published. The guarded initial
connection and one actual systemd verification both returned market_closed with
zero Theta requests. At 13:11:19 EDT the service finished successfully; the timer
was enabled and active/waiting with next wake-up September 28 at 09:00 EDT.
The new derived store had zero provider attempts and zero observations. Fifteen
focused tests and fresh independent correction review passed; primary API,
browser, build, and offline Web Push checks passed. See the monitor contract and
.local/options-monitor-build/activation.json. This proves connection and
activation, not live Theta throughput or browser delivery.

Options Monitor storage follow-up, September 26: the user explicitly selected 25
watchlist tickers per user, 100 unique across the Site, and rolling 90-session
intraday retention. The bounded update personalizes watchlists, deduplicates
collection, shortens diagnostic request retention to seven sessions, and removes
acknowledged delivery copies. Provider caps, roster, cadences, timer and audience
remain unchanged. A requested locked immutable monitor-store audit found 76 KiB
and no market observations. Offline synthetic sizing and validation are recorded
in [the storage report](OPTIONS_MONITOR_STORAGE_2026-09-26.md). No live provider
request, canonical mutation, or manual recurring-unit run was performed.


Options Monitor daily-history follow-up, September 26: the user explicitly
requested implementation of daily ETF charts from the existing options.sqlite
history. The private read-only consumer and derived Site cache are recorded in
[the monitor contract](OPTIONS_MONITOR_2026-09-26.md#daily-historical-charts--september-26-follow-up).
The initial export is capped at capture ID 37863, 80 Site POSTs, 30 minutes,
zero ThetaData calls, and no hidden retry. Existing out-of-session clock cycles
may export one page of at most 500 records; no unit is manually run, restarted,
or rescheduled. The canonical source stays unchanged. This decision does not
authorize new historical provider collection; completion evidence follows the
actual export and private deployment.


Options Monitor daily-history completion (September26 Eastern): private Site
commit921d8a981bb1d0f14d7cfc9a8e04659be69182ac deployed successfully at
2026-09-27T00:20:33.767945+00:00. Initial read-only export completed in76batches,
37,863records, source checkpoint37863, zeroThetaData calls, no hidden retries.
Cloud checkpoint readback and unchanged canonical metadata/no-WAL checks passed.
Fresh independent source/SQL review and focused Python/API/browser/build gates
passed. No unit execution or restart occurred. See the monitor contract and
.local/options-monitor-history/ for exact evidence and limitations.


<a id="recorded-theta-collection"></a>

## September 26, 2026 — daily Theta options and weekend repair

The user explicitly requested daily ThetaData collection into the existing
options.sqlite shape and weekend repair of gaps in the past week, following the
price-repair pattern. The scope remains the existing 15 ETFs. Implementation
is complete. After automatic approval review required a separate activation
decision, the user explicitly replied "Install and enable both timers." This
authorizes the exact schedules below with no immediate manual fetch and does
not reopen the completed historical populations.

The [daily/weekly contract](THETA_DAILY_COLLECTION_2026-09-26.md) fixes daily
19:15 Eastern, Sunday 03:00 Eastern, 32/152 data requests, 2/8 GiB and
20/60-minute limits, one request at a time, and zero automatic retries. Weekly
publication is missing-only for the immediately preceding Monday-Friday
window; historical dates before September 21 are excluded. Existing records
are preserved under the physical store lock. Validation covers 98 distinct
passing tests (26 final scheduler tests plus 72 unaffected options/readers);
fresh independent review passed after a durable-admission correction. The
immutable preflight matched the schema and latest capture shape without source
changes or provider calls. Following explicit activation approval, both timers
were installed/enabled and active/waiting at 2026-09-27T03:20:07Z. Next triggers:
Sunday September 27 at 03:00 EDT and Monday September 28 at 19:15 EDT. Neither
service has executed; no manual fetch occurred, and canonical stamps were
unchanged. Live provider success remains unobserved. See the focused contract
and .local/theta-daily-20260926/activation.json for actual evidence.


<a id="recorded-alpaca-retirement"></a>

## September 26, 2026 — retire Alpaca options collection; Theta agent source

The user explicitly requested that options data for new agent tools come from
options.sqlite and that the Alpaca options collection be retired. The idle
Alpaca timer was disabled/stopped; its linked installed definition was removed
by systemd disable and its transient failed state cleared without a service
start. At 2026-09-27T03:42:50Z it was inactive/not-found with no next trigger.
Historical Alpaca data and repository unit definitions were preserved. No
provider request, canonical-store access, deletion, unrelated Alpaca/news
change or Theta/monitor timer change occurred.

The three existing options v2 reads remain explicit legacy Alpaca interfaces,
not Theta tools. Current agent guidance excludes them from new Theta research.
The [proposed Theta toolkit](THETA_AGENT_TOOL_PROPOSAL_2026-09-26.md) fixes
options.sqlite as the only options source, with no fallback, and recommends
coverage, daily history and daily snapshots first. These new tools are not yet
implemented or advertised. Exact runtime discovery and retirement evidence is
in .local/options-tool-review-20260926/.


## September 27, 2026 — local Theta options agent toolkit

The user's "OK great, pls implement" authorizes the six proposed local read-only
Theta tools. Their [version 1.0.0 contracts](THETA_AGENT_TOOLS_2026-09-27.md)
are registered in system registry 2.93.0. The fixed source is the existing
optional options.sqlite through its immutable reader and physical lock.
This authorizes local tool access, not new network hosting, provider calls,
manual collector executions, canonical changes, or recurring-unit changes.
The prior Alpaca retirement remains in effect; archived tool semantics and
saved evidence remain intact. Validation and read evidence are retained in
.local/theta-agent-tools-20260927/; implementation existence alone is not a
claim of provider success or any additional operational activation.


## September 28, 2026 — options monitor startup repair and one calendar recovery

After diagnosing the missing dotenv dependency and blocked calendar admission,
the user explicitly approved the tested runtime repair plus one calendar recovery
for September 28, with no retry and a two-minute ceiling. The isolated monitor
runtime now includes cached python-dotenv 1.2.3; import preflight precedes provider
admission and startup/blocked-calendar failures publish a sanitized failure state.
Existing unit schedule, roster and recurring bounds remain unchanged.

The single authorized recovery completed with one calendar data request, 59
received bytes, zero manual option snapshots, no retry, and no canonical write.
It used the existing Theta credential resolver, a 1 MiB/90-second budget and
120-second supervisor, and preserved the original 09:30 failed admission under
a separate explicit recovery identity. The 11:20 normal clock trigger skipped
because recovery held run.lock; no service was manually started. See the monitor
contract and .local/options-monitor-repair-20260928/ for evidence and subsequent
normal-clock publication observations. This approval authorizes no further
manual provider unit or retry.


September 28 monitor verification: the normal 11:25 ET run published 240 records
in three batches, confirmed by private Site readback. It remained partial: 1,000
OHLC authentication failures, one OI unavailable response, and only one published
record with usable volume. Startup/import and upload recovery are proven, full
market-data coverage is not. The one approved manual calendar request is consumed;
no manual option snapshot or further retry has occurred. A separately proposed
one-SPY snapshot diagnostic is pending, not operationally authorized by this note.


September 28 additional explicit approval: the user replied "Go ahead" to one
SPY option_snapshot_ohlc diagnostic (16 MiB, two minutes, no retry, no raw retention)
and correction of the remaining integration issues. The single request completed
at approximately 12:06:43 ET with 168,087 bytes; the preceding run.lock refusal
made no provider request. Its approval is consumed. The tested monitor-only
mixed-session OHLC interpretation and shared SDK login were applied at 12:11:34 ET,
without changing recurring schedules, roster or caps. See the monitor contract
and integration-applied.json; no extra manual provider run is authorized.


September 28 integration verification: the ordinary 12:15 ET cycle completed
successfully with four broad-ETF observations, eight successful data requests,
zero request errors and one acknowledged Site publication. All four have usable
volume, put/call and 30-day IV. Cloud state confirmed complete at
2026-09-28T16:15:05.932Z; outbox was empty. This establishes corrected ETF data
and upload operation, not full-universe throughput or mature anomaly baselines.
See .local/options-monitor-repair-20260928/integration-completion.json.


## September 28, 2026 — staggered options radar approved and applied

The user explicitly approved six staggered groups for ordinary 30-minute
universe monitoring, releasing one group every five minutes while preserving
ETF/watchlist/liquidity/alert priorities. This supersedes aligned eligibility for
ordinary radar names only; the existing five-minute recurring unit, frozen
roster, credentials, request/byte/duration/worker caps and zero-retry authority
remain unchanged. No additional manual provider execution is authorized.

The 46-test isolated candidate was applied under the idle run.lock at
2026-09-28T12:40:54.710239-04:00. The original request admission identities are
preserved; observations/baselines use the shifted time. No timer edit, manual
service execution/restart, canonical mutation, schema change or Site deployment
occurred. Next normal ticks load the change; full-rotation live throughput has
not yet been observed. See the monitor contract's staggered-collection section
and .local/options-monitor-stagger-20260928/applied.json for exact evidence.


<a id="recorded-options-monitor"></a>

## September 28, 2026 — interval-volume correction and Site UI approved/applied

The user explicitly approved full-chain cumulative call/put deltas, including
newly active contracts, and the corresponding Site chart changes. This supersedes
strict contract-fingerprint equality for volume intervals only. Compact coverage
counts, separate side corrections and snapshot/time checks retain failure gaps.
Actual longer/cadence-transition intervals may be displayed but are excluded from
normal-cadence anomaly baselines and alerts. No raw-chain persistence or historical
rewrite was authorized or performed.

Site version 6 deployed successfully with existing custom sharing preserved;
tested collector source was applied under idle run.lock at
2026-09-28T15:50:56.036521-04:00. Validation: 57 Python tests, 23 Site adapter/API
checks, TypeScript, production build and desktop/mobile browser review. Existing
normal clock execution loads the change; no manual provider request, unit
execution/restart, schedule or recurring-bound change occurred. This decision
grants no new provider unit or retry. Post-application normal-clock observation
readback remains pending at this record. See the monitor contract and
.local/options-monitor-interval-20260928/applied.json.


## September 29, 2026 — five Site dashboard improvements completed

The user explicitly approved all five dashboard priorities and subsequently
requested continuation after WSL recovered, while preserving the decision not to
restart it. Bounded Site-only implementation added scoped coverage/warm-up,
activity rankings and radar filters, linked ETF heatmap, prior-session expected
volume comparisons, and exact alert-time evidence/chart navigation. It reads
existing saved aggregates and adds no storage schema or raw-data retention.

Version 7, source 7a7d33c49903efefcf0f4b5ed20393be61f29d94, deployed successfully
at 2026-09-29T14:58:56.519034Z (appgdep_6abbd228849c81918e769cee5e4e7490).
Existing custom audience was preserved. Validation passed 83 focused calculation,
local API and SQL/regression checks, TypeScript, production build, final source
review and 13 grouped local browser scenarios with desktop/mobile visual review.
This is deployment evidence, not a new live-provider or delivery verification.

No WSL/service restart, collector edit, manual provider call, recurring-unit or
schedule change, canonical write, production fixture write or parent repository
commit/push occurred. Only the Site workflow committed/pushed the nested Site.
This decision grants no extra provider unit or retry. See the monitor contract's
five-priority section and .local/options-monitor-five-priorities/completion.json.


September 29 Site visual refinement: the user requested improved ETF history
presentation. The two-file chart/CSS update was published as version 8, source
c0126b1d28a1e18f4cadedf7ca800e7a22070fd6, deployment
appgdep_6abbdf1ede648191a9fc1f5262fb1f7c (succeeded at 15:54:16Z).
Existing custom sharing was preserved; no provider unit, scheduler, collector,
API/schema, storage or restart authority was added or exercised. Thirteen
existing chart-adapter checks, 14 grouped local browser scenarios, TypeScript,
build and visual review passed. See the monitor contract and
.local/options-monitor-history-visual/completion.json.
