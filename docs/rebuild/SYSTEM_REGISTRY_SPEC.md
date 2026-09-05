# System Registry Specification

## Status

**Accepted.** The canonical registry path is
`config/system_registry.json`; the optional host override remains
`QUANT_SYSTEM_REGISTRY_PATH`. The current accepted configuration is revision
`2.69.0`, schema `1.9.0`, with 44 migrations, 59 datasets, and 66 collectors;
registry SHA-256
`2e9c3e4d2bfc263735a1e9c875d2091210065e0a375a0a0e0c420839a03c774f` and catalog `2.26.0` SHA-256
`fcfb29de2c2138995918e40c603704a0b2df4c17b6c3229312734bb46b0f2a28`.
The former `2.22.0`/`validated` working candidate is
rejected under [ADR 0011](../adr/0011-retire-proposed-bls-cpi-release-archive.md)
and is not an accepted registry revision. Registry validation and artifact
presence are declarative, not authorization or evidence of provider execution,
canonical publication, public exposure, or scheduler operation. Because the
candidate never entered the active configuration lineage, it remains absent
from the later additive `2.23.0` through `2.68.0` revisions. For any
operational task, first read the
[current operating envelope](CURRENT_OPERATING_ENVELOPE.md).

The documented lineage preserves the Stage 8 set of 57 tools, four
local-private dashboard exposures, eight disabled
`manual_fixture_only` jobs, and one bounded `manual_only`/fixture-only JSON
Atlas export. It also declares the isolated Stage 9, private Stage 10
market-history, private Stage 11 BEA/EIA candidate, and bounded
fixture-validated FMP stock-latest news resources. Those earlier private
datasets have no tool, dashboard, or Atlas exposure. The `2.63.0`
current-news datasets are exposed only through local read-only
`news.search@2.0.0`; they do not expose bodies or raw evidence. Registry
`2.64.0` adds the generic active current-news datasets and separately adopts
`news.fmp.stock_latest_legacy_articles` as inactive private evidence with no
collector, tool, dashboard, export, or source ID. Version 2.1 reads only the
fixed source IDs declared by the registry. Neither public version exposes
article bodies or raw evidence, performs a provider request, or reads the
legacy relation.

Registry `2.65.0` adds forward market migration
`market:0011_option_raw_evidence` at SHA-256
`f6a4685963e1eddffb7fd92944e19e4bbeda0c89bc196d85a2308f1f408d8129`
and the private `market.alpaca.option_raw_evidence` dataset. The dataset owns
immutable exact-response BLOBs and capture memberships and is an output of
both Alpaca option collectors; it has no tool, dashboard, export, or public
route. The collectors now recognize Alpaca's explicit single-equity,
100-share, 100%-allocation representation as an ordinary standard
deliverable. True adjusted, mixed, cash, alternate-root, or non-100-share
contracts remain analytically excluded, but their received response bytes are
still retained. The migration narrowly relabels only provably affected stored
contracts whose delivery symbol matches the stored underlying; because older
response bytes were not retained, it records explicit missing reasons instead
of inventing historical prices. Exact projection removes only this migration,
dataset, collector bindings, and semantic-identity field and restores
byte-exact registry `2.64.0` at SHA-256
`b47b6ad63ecaa41477388af033c7f928083ceb5e7db17bf76ff4ab99f71f3dc4`.
The tool catalogs remain unchanged.

Registry `2.66.0` adds only local read-only news/research contracts:
cursor-paginated `news.search@2.2.0`, seven additive news names, and
`research.news_event_impact@1.0.0`. The tools read retained current-news
metadata, and the impact tool additionally reads retained daily market prices;
they perform no provider request or canonical write. Clusters are exact-match
candidates, coverage uses provider symbols without claiming canonical entity
resolution, labels are deterministic derived annotations, and event impact is
retrospective raw close-to-close performance rather than a causal or abnormal
return claim. The current inventory is 73 logical names, 41 version policies,
58 variants, and 148 catalog contracts. Exact projection removes only these
tools and restores byte-identical registry `2.65.0` and catalog `2.24.0`.

Registry `2.67.0` adds the retained-only
`data.get_dataset_status@1.0.0` contract and explicit `2.0.0` successors for
`options.search_captures`, `options.search_contracts`, and
`options.get_surface_snapshot`. Dataset status evaluates retained successful-
capture anchors against declared thresholds; it does not probe providers,
credentials, or schedulers. Options v2 reads only the fixed retained Alpaca
paper/indicative ETF cohort, never mixes captures, keeps raw responses private,
and preserves missing/nonstandard exclusions explicitly. The current inventory
is 74 logical names, 44 version policies, 61 variants, and 156 catalog
contracts. Exact projection removes only this increment and restores byte-
identical registry `2.66.0` and catalog `2.25.0`.

Registry `2.68.0` adds six private, credential-free, bounded `manual_only`
macro-history collectors for CFTC TFF and disaggregated futures-only history,
Treasury securities auctions, NY Fed Primary Dealer Statistics, and Federal
Reserve H.8 and SLOOS. They bind only to existing
`fixture.macro.rtdsm_employ_evidence`, `fixture.macro.rtdsm_employ`, and
`fixture.macro.stage3_catalog`, derive physical locks from those outputs, allow
one attempt without retry, and occur in no job. The H.8 and SLOOS
attempts use seven and six manifest-ordered singleton requests respectively
under their unchanged 16 MiB aggregate response cap. The increment adds no migration,
dataset, public tool, dashboard, export, catalog contract, or scheduler. Exact
projection removes only these six declarations and their reciprocal bindings,
restoring byte-exact registry `2.67.0` at SHA-256
`a80b0e06db95968c9fd49cd3d90054b709c57895993a28b512ba2550e162f325`; catalog `2.26.0` remains unchanged.

Registry `2.69.0` adds only `fmp.company.corporate_actions_current` and
`fmp.company.analyst_estimates_current` as private, bounded, manual-only
collectors, with reciprocal bindings to existing company action and expectation
evidence/fact datasets. It adds no schema, migration, dataset, tool or catalog.
Exact projection restores `2.68.0` at SHA-256
`9b59f6b643e4cff7390559763c8532215ac9927a1f3119870385127af3a6a27e`.
[The repair receipt](FETCH_REPAIRS_2026-09-05.md) owns finite publication
evidence and the separately approved company host scheduler exception.

Registry `2.13.0` also

declares only the offline fixture collector
`market.stage12b.fmp_daily_incremental_fixture` with handler
`market.stage12b_fmp_daily_incremental_fixture`, as defined by the
[Stage 12B contract](STAGE12B_INCREMENTAL_MARKET_V1.md). It selects
`data/market.sqlite` as the current market default; neither declaration opens
or accepts an existing file, and the Stage 12B declaration has no live provider,
API-key, network, default/retained-store, migration, public-consumer, or
scheduler authority.
Registry `2.14.0` adds only
`market.stage12c.fmp_daily_incremental_manual`, defined by the
[Stage 12C contract](STAGE12C_MARKET_GAP_V1.md). Stage 12C is complete and
independently verified; the binding provider outcomes and final target state
are in its [immutable evidence record](STAGE12C_EVIDENCE.md), not inferred from
the declaration. Revision `2.14.0` adds no migration, public exposure, or
scheduler declaration. Its exact historical projection restores `2.13.0`
before Stage 12B and earlier evidence. Stage 12D is complete and independently
verified under its
[no-transfer adoption/freeze contract](STAGE12D_PROJECT_LOCAL_OPERATIONALIZATION.md)
and [evidence record](STAGE12D_EVIDENCE.md); it did not change this registry.
Registry `2.15.0` changes only the remaining current store defaults to
`data/macro.sqlite`, `data/company.sqlite`, and `data/news.sqlite`, matching
the files already present beside `data/market.sqlite`. It creates, copies,
moves, opens, or mutates no SQLite file. Its exact historical projection
restores registry `2.14.0` and the three prior `_data` declarations before
Stage 12C/D or earlier evidence is reproduced.
Registry `2.16.0` adds macro ordinal 13, two private datasets, and the fixed
official BEA/BLS GDP/CPI vintage collectors. Registry `2.17.0` adds macro
ordinal 14 and only the private Philadelphia Fed historical and BLS current
employment collectors. Registry `2.18.0` adds macro ordinal 15 and two
manual-only deep-history collectors; both successor revisions reuse the two
private official-vintage datasets. Registry `2.19.0` adds only the manual-only
`fmp.macro.gdp_cpi_release_calendar_history` collector, reusing
`fixture.macro.economic_calendar` and the existing `macro.official_vintages`
tool dependency. It adds no migration, dataset, job, scheduler, new public-
consumer identity, or surprise table. The existing `macro.release_surprises`
tool is now established over the calendar and official-vintages relations. The
`2.19.0` declaration adds no new tool, dashboard, export, credential, or
caller-selected path. On demand, GDP selects one best-available record per
quarter: an exact-date BEA first release (`advance`, or source-native
`initial`), otherwise an exact-date `second`, then an exact-date `third`.
Every selection compares FMP consensus with the BEA actual from the same
release date and stage; FMP GDP actual is never used. Equivalent reviewed
aliases collapse only when their same-date values agree; conflicts fail
closed. Later stages are explicitly marked `is_fallback`. The current
canonical read returns all 55 quarters from `2012Q4` through `2026Q2`: 10
advance, one initial, 10 second, and 34 third; 54 have numeric surprises and
`2012Q4` is `missing_consensus`. At that revision, the 864 CPI results use
actual and consensus from the same FMP event version. This derived policy adds
no migration, physical table, provider request, or scheduler. At revision
`2.19.0`, the inventory was 37 migrations, 50 datasets, and 35 collectors.
The exact
`2.19.0` to `2.18.0` projection removes only the FMP calendar collector; the
`2.18.0` to `2.17.0` projection removes only
migration 0015 and the two history collectors; the `2.17.0` to `2.16.0`
projection removes only
migration 0014 and the two employment collectors; the existing `2.16.0` to
`2.15.0` projection then removes only the GDP/CPI vintage slice.

Registry `2.20.0` adds only
`fmp.macro.employment_release_calendar_refresh` and its reciprocal binding to
the established economic-calendar dataset. It adds no migration, dataset, job,
timer, surprise table, or public route. A separate explicit user decision
authorized the fixed refresh unit recorded in the
[current operating envelope](CURRENT_OPERATING_ENVELOPE.md); that host-level
scheduler state is not a registry declaration. Its exact projection restores
byte-exact `2.19.0`.

Registry `2.21.0` adds macro migration
`macro:0016_fmp_calendar_wholesale_evidence`, the private
`macro.fmp.economic_calendar_evidence` dataset, and the manual-only
`fmp.macro.us_economic_calendar_wholesale` collector. The dataset owns only
`fmp_economic_calendar_captures` and `fmp_economic_calendar_rows`; it adds no
public tool, dashboard, export, job, timer, or surprise table. The canonical
inventory is now 38 migrations, 51 datasets, and 37 collectors. Its exact
`2.21.0` to `2.20.0` projection removes only those three declarations and
restores byte-exact `2.20.0`.

ADR 0011 rejects the never-active `2.22.0` candidate
`macro:0017_bls_cpi_release_archive`, its archive datasets, collector, and
relations. It is not part of the 38/51/37 inventory, has no migration
allocation or accepted completion evidence, and must not be activated. CPI
surprises use FMP actual and consensus under the documented complementary-row
repair rule; the retired archive cannot validate, fill, replace, or otherwise
affect them. The completed `2.16.0` BLS annual-revision snapshots and current
BLS refresh remain separate accepted declarations.

Registry `2.23.0` adds only the manual-only
`fmp.macro.treasury_yield_curve_history` collector and reciprocal bindings to
the existing generic macro evidence/catalog datasets and Treasury-curve
dataset. Registry `2.24.0` then adds only the credential-free, manual-only
`nyfed.macro.overnight_rates_history` collector and reciprocal bindings to the
three existing generic macro datasets. It stores only the EFFR, OBFR, TGCR,
BGCR, and SOFR headline percentages in existing macro relations;
distributions, volumes, averages/index values, and facilities remain outside
revision `2.24.0`.

Registry `2.25.0` adds only the credential-free, manual-only
`nyfed.macro.repo_facility_usage_history` collector and reciprocal bindings
to the same three generic macro datasets. It stores daily ON RRP and SRF
accepted USD amounts in the existing macro evidence, release, snapshot,
version, and current-observation relations. Explicitly identified small-value
and operational-readiness exercises are excluded; same-day operations for one
facility are aggregated. It adds no migration, dataset, job, timer, public
tool, dashboard, export, credential, or caller-selected path. At revision
`2.25.0`, the inventory was 38 migrations, 51 datasets, and 40 collectors.
Exact projection
removes the repo-facility declaration and bindings to restore byte-exact
`2.24.0`, then removes the headline-rate declaration and bindings to restore
byte-exact `2.23.0`, before the Treasury projection restores the established
pre-Treasury registry.

Registry `2.26.0` adds only the credential-free, manual-only
`nyfed.macro.soma_summary_history` collector and reciprocal bindings to the
existing SOMA evidence and summary datasets. One bounded aggregate-summary
response is normalized into nine declared weekly components in the existing
`soma_source_artifacts`, `soma_snapshots`, `soma_snapshot_artifacts`, and
`soma_summary_components` relations. Source-empty values remain explicit
missingness; security-level identity is rejected. It adds no migration,
dataset, job, timer, public tool, dashboard, export, credential, or
caller-selected path. The current inventory is 38 migrations, 51 datasets,
and 41 collectors. Exact projection removes only the SOMA collector and its
two reciprocal bindings to restore byte-exact `2.25.0`, after which the
existing projection chain applies unchanged.

Registry `2.27.0` adds only three credential-free, manual-only collectors
and reciprocal bindings to the three existing generic macro datasets:
`federal_reserve.macro.h41_history` for three H.4.1 series delivered in one
FRED ZIP, `chicagofed.macro.nfci_history` for NFCI and ANFCI in one CSV, and
`bis.macro.credit_conditions_history` for three U.S. private-sector credit
series in two CSV responses. It adds no migration, dataset, job, timer, public
tool, dashboard, export, credential, or caller-selected path. That revision's
inventory is 38 migrations, 51 datasets, and 44 collectors, and its registry
source SHA-256 is
`9be40d07a7984b223303174a079b19a2a57fa7fcb5a56678482533dc53e4f8ff`.
Exact projection removes only those three collectors and their reciprocal
bindings to restore byte-exact `2.26.0`.

The later Federal Reserve policy-rate fast-path extension reuses the existing
credential-free `federal_reserve.macro.h41_history` collector binding and its
generic macro publisher for a separate one-response FRED source containing
IORB and the federal-funds target bounds. It does not change registry
declarative content, schema, datasets, locks, credential handling, or the
historical H.4.1 request.

Registry `2.28.0` adds only the credential-free, manual-only
`nyfed.macro.cmdi_history` collector and reciprocal bindings to the same
three existing generic macro datasets. One fixed NY Fed workbook supplies the
overall-market, investment-grade, and high-yield Corporate Bond Market
Distress Index series. It adds no migration, dataset, job, timer, public tool,
dashboard, export, credential, or caller-selected path. The current inventory
at that revision was 38 migrations, 51 datasets, and 45 collectors, and its
registry source SHA-256 was
`131b4f24b2e8d7d9dfa7fedb5de37cf16455aa5a0ee91d0aae69315fd0ac2aef`.
Exact projection removes only the CMDI collector and its reciprocal bindings
to restore byte-exact `2.27.0`.

Registry `2.29.0` adds only three manual-only collectors and reciprocal
bindings to the same three existing generic macro datasets. Treasury Fiscal
Data supplies the daily Treasury General Account closing balance in one fixed,
credential-free request. EIA supplies the weekly Lower-48 working natural-gas
storage series in one fixed request through the existing `EIA_API_KEY`
resolver. NBER supplies one fixed business-cycle chronology response, which is
converted at capture time into a monthly 0/1 U.S. recession indicator. It adds
no migration, dataset, job, timer, public tool, registry dashboard, export,
credential mechanism, or caller-selected path. The inventory at that revision was 38
migrations, 51 datasets, and 48 collectors, and its registry source SHA-256 was
`d7a5ba0a556abc9faa6d726e162969d4c11f0a5da614865eff23318d16ca55ff`.
Exact projection removes only these three collectors and their reciprocal
bindings to restore byte-exact `2.28.0`; the earlier projection chain remains
unchanged.

The later Treasury Debt to the Penny fast-path extension reuses the existing
credential-free `treasury_fiscal_data.macro.tga_closing_balance_history`
collector binding and generic macro publisher for a separate one-response
Treasury Fiscal Data source containing total public debt, debt held by the
public, and intragovernmental holdings. It does not change registry declarative
content, schema, datasets, locks, credential handling, or the historical TGA
request.

The later Monthly Treasury Statement fast-path extension likewise reuses that
same Treasury collector binding and generic macro publisher for a separate
one-response summary source containing monthly federal receipts, outlays, and
deficit/surplus in millions of USD. It does not change registry declarative
content, schema, datasets, locks, credential handling, or either earlier
Treasury request.

The later EIA petroleum fast-path extension reuses the existing
`eia.macro.natural_gas_storage_history` collector binding, `EIA_API_KEY`
resolver, and generic macro publisher for three separate one-response sources:
weekly U.S. total-motor-gasoline ending stocks (`PET.WGTSTUS1.W`), distillate-
fuel-oil ending stocks (`PET.WDISTUS1.W`), and finished-motor-gasoline product
supplied (`PET.WGFUPUS2.W`). It does not change registry declarative content,
schema, datasets, locks, credential handling, or the existing natural-gas and
Stage 11 crude-oil requests.

Registry `2.30.0` adds only the credential-free, manual-only
`bls.macro.price_wage_productivity_history` collector and reciprocal
bindings to the same three existing generic macro datasets. One BLS API POST
requests `WPSFD4` Producer Price Index - Final Demand, seasonally adjusted;
`CES0500000003` Average Hourly Earnings - Total Private, seasonally adjusted;
and `PRS85006092` Nonfarm Business Labor Productivity, percent change from
the previous quarter. It adds no migration, dataset, job, timer, public tool,
registry dashboard, export, credential, credential mechanism, or
caller-selected path. The current inventory is 38 migrations, 51 datasets,
and 49 collectors, and that revision's registry source SHA-256 is
`4945c54e695b093112e6e7425dc214e5288396cf309d23ccf1aba33e386ee1e6`.
Exact projection removes only this collector and its reciprocal bindings to
restore byte-exact `2.29.0`; the earlier projection chain remains unchanged.

Registry `2.31.0` adds only macro migration
`macro:0017_live_gdi_vintages`. The additive successor widens the existing
official BEA vintage relations and provenance checks to admit real GDI
quarter-over-quarter annualized growth (`A261RL`) and nominal GDI
(`A261RC`) alongside GDP. The existing BEA collector reads both from its
single GDP/GDI workbook response, so no provider request, credential,
dataset, collector, job, export, public route, or caller-selected path is
added. In the same implementation increment, the existing one-response NY
Fed overnight-rate collector retains SOFR distribution percentiles, volume,
index, and 30/90/180-day averages; the existing one-response Chicago Fed
collector retains NFCI risk, credit, and leverage components; and the
established Stage 11 EIA electricity-retail publisher is made available to
the fixed aggregate refresh. At revision `2.31.0`, the inventory is 39 migrations, 51
datasets, and 49 collectors, and the registry source SHA-256 is
`d28298c36ce6b418ec516845ac3f58c8b9e4c0706afdacbb5f752ed7d5431bd0`.
Exact projection removes only migration 0017 and restores byte-exact
`2.30.0`; the earlier projection chain remains unchanged.

Registry `2.32.0`, schema `1.9.0`, adds version-policy syntax for public tools
without expanding or rebinding the 57 logical names. The top-level v1
declarations remain the default for omitted `tool_version` selectors and the
frozen 114-contract v1 catalog remains byte-identical at SHA-256
`a2469c903cc6c9dae64ea29c4d3b543837a37d4989277290220061101d28de87`.
Only `market.get_returns` and `market.get_forward_returns` gain explicit
`2.0.0` variants, backed by the three existing private Stage 10 datasets and a
separate four-contract v2 schema catalog. Their legacy `1.0.0` variants remain
selectable and discoverable as deprecated, with deterministic replacement
metadata and no scheduled removal milestone. The revision adds no migration,
dataset, collector, provider, credential mechanism, job, timer, export, or
deployment. Its registry source SHA-256 is
`4b57a6bfb311001eee852f19ca45ad500095c7f997d804860cdd24a5ef24565d`.
Exact projection removes only the version-policy/catalog declarations and the
three reciprocal Stage 10 tool bindings, restoring byte-exact `2.31.0` at
SHA-256 `d28298c36ce6b418ec516845ac3f58c8b9e4c0706afdacbb5f752ed7d5431bd0`.

Registry `2.33.0` adds `2.0.0` variants of `timeseries.describe`,
`timeseries.align`, and `timeseries.correlation` without changing the schema
version or the 57-name inventory. These variants accept the exact public
Stage 10 return-series composition schema, declare no store or dataset binding,
and operate only on caller-supplied typed values. The stricter composition
schema leaves the frozen `2.32.0` return-output contracts byte-identical. Their
typed contracts require explicit limits and alignment policy, reapply `as_of`
cutoffs to instrument and observation availability, preserve missing reasons
and indexed input lineage, reject contradictory return/temporal contracts, and
reject truncated correlation samples. The v2
catalog grows from four to ten contracts, advances to catalog version `2.1.0`,
and has SHA-256
`3ae30774d87c31217204da2240a56124c2e732a14f9fb6e38a57e3d6d7341b79`.
The frozen v1 catalog remains byte-identical. The revision adds no migration,
dataset, collector, provider, credential mechanism, job, timer, export, or
deployment. Its registry source SHA-256 is
`eae80b10840afa2b724215aa431e1b8feab303486bbc87a60a8591e5812536cb`.
Exact projection removes only the three composable policies and restores
byte-exact registry `2.32.0` at SHA-256
`4b57a6bfb311001eee852f19ca45ad500095c7f997d804860cdd24a5ef24565d`.

Registry `2.34.0` adds `2.0.0` variants of `econometrics.regression`,
`econometrics.rolling_regression`, and `econometrics.stationarity` without
changing schema version or the 57-name inventory. They accept compatible,
non-truncated trailing Stage 10 v2 return series and declare no store or
dataset binding. OLS publishes classical homoskedastic covariance, Student-t
inference, fixed 95% confidence intervals, fit diagnostics, and numerical
backend metadata. Rolling OLS reuses that exact kernel over fixed contiguous
complete windows. Stationarity is limited to a constant-only, explicit
fixed-lag augmented Dickey-Fuller regression with MacKinnon 2010 finite-sample
critical values, three explicit rejection decisions, and no approximate
p-value. The v2 catalog grows from ten to sixteen contracts, advances to
catalog version `2.2.0`, and has SHA-256
`5250c18b70066de734cb2a4715ec20825f4a587892ba70728065dde82a4a239c`.
The frozen v1 catalog remains byte-identical. The revision adds no migration,
dataset, collector, provider, credential mechanism, job, timer, export, or
deployment. Its registry source SHA-256 is
`9ed2affcaa84c6420c7650c10a02361fad2bd892b33a5df2797e033e300ef86d`.
Exact projection removes only the three econometrics policies and restores
byte-exact registry `2.33.0` at SHA-256
`eae80b10840afa2b724215aa431e1b8feab303486bbc87a60a8591e5812536cb`.

Registry `2.35.0` adds one private, manual-only collector,
`bea.macro.personal_income_history`, bound reciprocally to the three existing
generic macro evidence/canonical/catalog datasets. Its fixed handler makes one
BEA NIPA `T20600`, monthly, `Year=ALL` request and retains only `A065RC`,
`A067RC`, and `DPCERC` as current-dollar USD millions at seasonally adjusted
annual rates. It uses `BEA_API_KEY` through the existing project credential
resolver, has a one-attempt/no-retry policy, a 16 MiB response bound, and a
5,000 selected-row bound. It adds no migration, dataset, provider family,
credential mechanism, registry job, tool, dashboard, export, or deployment.
Its source SHA-256 is
`a5e5b11bd9578428430c96f9eec59214e39f8cbb85f5b74fffc37aebaabdc27e`.
Exact projection removes only this collector and its reciprocal bindings,
restoring byte-exact registry `2.34.0` at SHA-256
`9ed2affcaa84c6420c7650c10a02361fad2bd892b33a5df2797e033e300ef86d`.

Registry `2.36.0` adds `2.1.0` variants of
`econometrics.regression` and `econometrics.rolling_regression` without
changing schema version, the 57-name inventory, datasets, collectors, or
migrations. The variants declare no store binding. They add HC1, HC3, and
fixed-lag Bartlett Newey-West covariance with asymptotic-normal inference and
fixed-lag Ljung-Box, Koenker-Breusch-Pagan, and Jarque-Bera residual
diagnostics; existing `2.0.0` variants remain unchanged. The v2 catalog grows
from sixteen to twenty contracts, advances to catalog version `2.3.0`, and
has SHA-256
`7de5126d50c438d0bb35cb82a9a0dd282251de3acceeb850d69ca20f894ef7b9`.
The frozen v1 catalog remains byte-identical. The operational inventory is 39
migrations, 51 datasets, and 50 collectors. The registry source SHA-256 is
`5c9702d8ca4c2f896083471cc60adecfd3a43c72d45694d04688ab07e6330035`.
Exact projection removes only the two `2.1.0` variants and restores
byte-exact registry `2.35.0` at SHA-256
`a5e5b11bd9578428430c96f9eec59214e39f8cbb85f5b74fffc37aebaabdc27e`.

Registry `2.37.0` adds only a `2.1.0` variant of
`econometrics.stationarity`. The ADF-only `2.0.0` variant is unchanged. The
new variant adds caller-fixed-lag level KPSS, the published 1992 asymptotic
critical values without an interpolated p-value, and a four-state joint
ADF/KPSS interpretation at one declared significance level. Catalog `2.4.0`
has 22 contracts at SHA-256
`529d904d46003c53785926730279dc4c37bf09b7d15f99c366122c416c4af26e`;
the registry source SHA-256 is
`2a2b611ac6f752e6d83a81369155454b1484b8caebf4d1aaa3be60c41f0e866b`.
Exact projection removes only that variant and restores byte-exact registry
`2.36.0`.

Registry `2.38.0` adds only the `2.0.0`
`econometrics.structural_breaks` policy. It declares one caller-supplied,
zero-based first post-break row and compares pooled, pre-break, and post-break
classical OLS with the Chow F statistic. It does not perform automatic break
search, multiple-testing adjustment, or multiple-break inference. Exact
finite-sample F inference requires Gaussian, homoskedastic, independent
errors and the standard exogenous fixed-design linear-model conditions.
Catalog `2.5.0` has 24 contracts at SHA-256
`e0650e5b76ec9a851220c2395c34f9172f1380b5a5c9b2bc681056c655bdeb5b`;
the registry source SHA-256 is
`3d6c0f31f2c72c20e5459c4e7f2358ea273437d59af99ef017b1f3ad47b1b547`.
At revision `2.38.0`, the inventory is 39 migrations, 51 datasets,
50 collectors, 9 versioned policies, and 12 variants. Exact projection removes only the new
policy and restores byte-exact registry `2.37.0`. Both revisions are
store-free and add no dataset, collector, credential, migration, job, timer,
export, or deployment; the frozen v1 catalog remains byte-identical.

Registry `2.39.0` adds only an explicit `3.0.0` variant of
`econometrics.regression` and operation graph
`tool_platform.econometrics.regression.v3`. One mode is selected per request:
directional two-step Engle-Granger cointegration for exactly two horizon-one
return series, a fixed-order constant reduced-form VAR for two through five
series, or a conditional directional Granger-causality F test over the same VAR
system. Lag order is caller-fixed from zero through four for Engle-Granger and
one through four for VAR/Granger; no automatic lag, direction, break, or model
search occurs. Engle-Granger reconstructs normalized log levels from compatible
simple or log returns, uses a constant first-stage regression and a no-constant
residual ADF, reports MacKinnon 2010 `tau_c` N=2 finite-sample critical values,
and intentionally provides no approximate p-value. VAR reports every equation,
lag matrix, and residual covariance. Granger reports a conditional exact F-tail
under the classical fixed-design assumptions and explicitly does not claim
structural causality.

Catalog `2.6.0` has 26 contracts at SHA-256
`2c9424a0ff9cda9130d559c2dacb6cef55ff9ff8537191285219d608411b7f24`;
the registry source SHA-256 is
`f7f445a3dbc991ca7b309ce306e5bc439403d929b96fb9931a22c036cb0eea85`.
At revision `2.39.0`, the inventory is 39 migrations, 51 datasets, 50 collectors,
9 versioned policies, and 13 variants. Exact projection removes only regression
`3.0.0` and restores byte-exact registry `2.38.0`. The new variant is
caller-supplied and store-free and adds no dataset, collector, credential,
migration, job, timer, export, public route, or deployment. The 57 public names,
omitted-v1 behavior, prior selected versions, and frozen v1 catalog are
unchanged.

Registry `2.40.0` retains that tool surface and adds the existing private FMP
incremental-calendar migration plus its evidence and canonical-event dataset
declarations. Its inventory is 40 migrations, 53 datasets, 50 collectors,
9 versioned policies, and 13 variants. Its registry source SHA-256 is
`72516749f56f962bea2a265ef4a917558ef6e757444ae5d679bc50d2d64e6ed7`.
Exact projection removes only the incremental-calendar declarations and
restores byte-exact registry `2.39.0` at SHA-256
`f7f445a3dbc991ca7b309ce306e5bc439403d929b96fb9931a22c036cb0eea85`.
These declarations do not authorize a provider request, credential use,
canonical-store operation, scheduler, deployment, or public exposure.

Registry `2.41.0` adds only the native read-only
`market.get_price_series` declaration and reciprocal bindings to the three
existing Stage 10 market datasets. The active inventory is 58 names: the
frozen recovered compatibility target remains 57, and the new name has
`additive_native_v1` status. It returns exactly four coherent typed series
(`open`, `high`, `low`, and `close`) for a server-resolved ticker;
inclusive `start_date` and `end_date` are independently optional. The v2
catalog advances to `2.7.0` with 28 contracts at SHA-256
`0f89da921b37d210c596646b3c4f47e4dbe27c2fe2579d8772c6db2bed312398`.
The current registry source SHA-256 is
`835fe846c0d0bf0ce630cda0dd23588983c83f6fad663cbe51202fe00deee2a3`.
Exact projection removes only the additive declaration and reciprocal tool
bindings and restores byte-exact registry `2.40.0` and catalog `2.6.0`.
This revision adds no migration, dataset, collector, provider request,
credential access, write path, scheduler, export, hosting, or deployment.

Registry `2.42.0` adds only the native read-only
`market.get_available_ticker` declaration and reciprocal bindings to
`market.stage10.instruments` and `market.stage10.daily_prices`. The active
inventory is 59 names while the recovered compatibility target remains 57.
Empty `{}` arguments request the full deterministically ordered bounded set;
optional `limit` is 1 through 10,000. A returned instrument must have a
current FMP/provider-native price pointer whose immutable version and capture
are retrievable. The result is current retained-data discovery, not a live or
historical point-in-time universe. Catalog `2.8.0` has 30 contracts at
SHA-256
`554873c79f58abbee6f4fbf83e4a6767153c9ff920651825d8cac3bd6075905f`;
the registry source SHA-256 is
`1ab956e8338b864873f25e6e41cc6a18ba9a271a1951bec0bdccf32a07b8fd2b`.
Exact projection removes only this declaration and its reciprocal dataset
bindings and restores byte-exact registry `2.41.0` and catalog `2.7.0`.
The fixed local subprocess bridge derives the project root, canonical registry,
and store routing host-side and exposes strict JSON only; it adds no URL,
caller-selected path, SQL, provider, credential, write path, migration,
scheduler, export, hosting, or deployment.

Registry `2.43.0` adds only the private
`alpaca.market.spy_option_surface` collector and reciprocal bindings to the
existing instrument, option-capture-evidence, and options datasets. It consumes
the existing Stage 10 instrument dataset, resolves the fixed FMP SPY identity,
and reuses the applied options schema without a migration. The declaration
names only `ALPACA_API_KEY` and `ALPACA_API_SECRET`; it stores no credential.
At most four single-attempt requests, 10,000 rows, 8 MiB, and 120 seconds are
allowed. Its exact semantic replay policy is zero persistent writes.
The registry collector remains `manual_only`; the separately authorized fixed
host timer is the only recurring exception. Exact projection removes only this
collector and its reciprocal bindings and restores byte-exact registry
`2.42.0` at SHA-256
`1ab956e8338b864873f25e6e41cc6a18ba9a271a1951bec0bdccf32a07b8fd2b`.
The current source SHA-256 is
`841041060550eeb41fed491c19835f77d278cbf68daaa9a7b2f754c3f9c4f0ff`.
No public tool, catalog schema, migration, export, hosting, or deployment is added.

Registry `2.44.0` adds only the private
`sec.company.aapl_fundamentals` collector and reciprocal bindings to the five
existing company SEC evidence, issuer, filing, filing-membership, and
fundamentals datasets. It fixes CIK `0000320193`, makes one submissions and one
CompanyFacts request with no retry, and names only `SEC_USER_AGENT_NAME` and
`SEC_USER_AGENT_EMAIL`; the registry stores neither value. The operation is
bounded to two requests, 10,000 normalized rows, 16 MiB total response bytes,
and a monotonic 120-second pre-write deadline. Network capture and parsing
complete before the physical
company-store lock, and exact semantic replay produces zero persistent writes.
An endpoint-identical response reuses its domain artifact and snapshot when
the other endpoint changes without changing its interpreted membership. A
material CompanyFacts response creates a new
complete snapshot and the schema-required run-cohort fact/fundamental versions.
The collector is `manual_only`; no company scheduler or recurring exception is
declared. Exact projection removes only this collector and reciprocal bindings
and restores byte-exact registry `2.43.0` at SHA-256
`841041060550eeb41fed491c19835f77d278cbf68daaa9a7b2f754c3f9c4f0ff`.
The current source SHA-256 is
`af6545258751f7b7a7e7c68c673e18a36c65762809032db6ea540b33f249c182`.
No migration, public tool, catalog schema, scheduler, export, hosting, or
deployment is added.

Registry `2.45.0` adds only the private
`sec.company.market_fundamentals` collector and reciprocal bindings to the
same five company datasets. Its one-issuer collector contract is bounded to
two single-attempt requests, 64 MiB, 50,000 selected core rows, and 120
seconds. The fixed host wrapper obtains the 519 Stage 10 equity symbols by an
immutable descriptor-pinned market-store read, makes one bounded official SEC
ticker-map request, deduplicates shared CIKs, and invokes the collector
sequentially at no more than five requests per second. The wrapper permits at
most 1,401 total requests and six hours; failures are isolated by issuer and
reported as bounded counts and digests. AAPL retains its exact `2.44.0`
semantic/source identities. The reviewed ten-metric map adds annual 20-F/40-F
support, and equivalent replay writes nothing. The collector remains
`manual_only`; the authorized scheduled refresh is implemented as a fixed
07:15 America/New_York weekday host-timer exception. Exact projection removes only this
collector and its reciprocal bindings and restores byte-exact registry
`2.44.0` at SHA-256
`af6545258751f7b7a7e7c68c673e18a36c65762809032db6ea540b33f249c182`.
The current source SHA-256 is
`f151db20dd26fe2123e887415736431cfe1dcda8bf8f42d83a8b47ad3a27fec2`.
No migration, public tool, catalog schema, export, hosting, or deployment is
added. Registry declaration and unit-file presence do not claim that the
authorized population or timer activation has completed.

Registry `2.46.0` adds two native local read-only declarations,
`market.get_volume_series` and `macro.get_release_calendar`, plus explicit
`2.0.0` policies for `macro.search_series`, `macro.describe_series`, and
`macro.get_series`. The active inventory is 61 names, 12 version policies,
and 16 variants. Catalog `2.9.0` has 40 contracts at SHA-256
`e6fa88fa63856247ab00073a14ff1321cfafa05d5323a22c26008e81d0b1eb5c`;
the registry source SHA-256 is
`b5236b88a2b320628b870fe3abe7898b76fa5fc1d527a223f87985963e38e264`.
The macro variants route the fixed official-vintage allowlist only to the
official model and other IDs only to the generic model. They use stored
current pointers for `latest`, stored availability under the requested
date-only cutoff policy for `as_of`, and explicit stored evidence for
`first_release`. Release-calendar retrieval supports `latest` and `as_of` but
does not claim first-release semantics; volume reuses the exact Stage 10 row
selection and has volume-specific output semantics. Exact projection removes
only these two names, their reciprocal bindings, and the three macro v2
policies, restoring byte-exact registry `2.45.0` at SHA-256
`f151db20dd26fe2123e887415736431cfe1dcda8bf8f42d83a8b47ad3a27fec2`
and catalog `2.8.0`. This revision adds no migration, dataset, collector,
provider request, credential, write path, scheduler, export, hosting, or
deployment.

Registry `2.47.0` adds only local, store-free `2.0.0` policies for
`data.quality_audit` and `timeseries.transform`, plus the four native
general-statistics tools `stats.distribution_diagnostics`,
`stats.covariance_matrix`, `stats.bootstrap_confidence_interval`, and
`stats.principal_components`. The active inventory is 65 names, 14 version
policies, and 18 variants. Catalog `2.10.0` has 52 contracts at SHA-256
`381a78aa59682fbf36cc90acc146d2cfa5b43ea90537b7356eca701df0794fbf`;
the registry source SHA-256 is
`eefa1288e8007518d466a3d4820522ae113ae6c52dc6df0e448cd3654de4a1b8`.
The quality and transformation variants consume caller-supplied typed return
series, while the native statistics contracts are deterministic in-memory
operations. Exact projection removes only the two version policies and four
native names, restoring byte-exact registry `2.46.0` and catalog `2.9.0`.
This revision adds no provider request, canonical-store operation, migration,
dataset, collector, credential, write path, scheduler, export, hosting, or
deployment.

Registry `2.48.0` adds only an explicit local, store-free `2.0.0` policy for
the existing reserved `market.technical_indicators` name. Omitted version
selection still routes to its frozen reconstructed v1 contract; explicit v2
accepts caller-supplied typed Stage 10 OHLCV series and calculates one
deterministic indicator specification per call. SMA, EMA, rolling sample
standard deviation and z-score, true range and ATR, rate of change and RSI,
MACD, Bollinger Bands, Donchian channels, stochastic oscillator, ADX, OBV,
and accumulation/distribution are supported. The active inventory remains
65 names and becomes 15 version policies and 19 variants. Catalog `2.11.0`
has 54 contracts at SHA-256
`864a4d07afbf2558a331d30275cf21f4e31521142ee2d9d010dc26cc5680a757`;
the registry source SHA-256 is
`3709c16168e2959a946c78e99c50b540b860d5f26ccf4afc3434831b8e9d8524`.
Exact projection removes only this version policy and restores byte-exact
registry `2.47.0` and catalog `2.10.0`. This revision adds no provider
request, canonical-store operation, migration, dataset, collector,
credential, write path, scheduler, export, hosting, or deployment.

Registry `2.49.0` adds only the private
`alpaca.market.etf_option_surface_grid` collector and reciprocal bindings to
the three existing market instrument, option-evidence, and canonical-option
datasets. The collector fixes 15 ETF underlyings, ten DTE targets, standard
call-and-put expiry eligibility, 80%-120%-of-spot contracts, explicit
nonstandard-surface exclusion, paper environment, IEX underlying snapshots,
and Alpaca indicative option snapshots. It is `manual_only`, has no registry job,
uses the existing Alpaca credential resolver and market-store lock/publisher,
and caps one run at 362 single-attempt requests, 900,016 rows, 256 MiB, and
900 seconds. The inventory is 40 migrations, 53 datasets, 54 collectors,
8 jobs, 65 tools, 15 tool-version policies, 4 dashboards, and 1 export. The
tool catalog remains `2.11.0` with 54 contracts at SHA-256
`864a4d07afbf2558a331d30275cf21f4e31521142ee2d9d010dc26cc5680a757`;
the registry source SHA-256 is
`6d34dc495de10de42765e8909e30df744f69ad72d1901259f7da67be2d2710e1`.
Exact projection removes only this collector and its reciprocal bindings and
restores byte-exact registry `2.48.0` at SHA-256
`3709c16168e2959a946c78e99c50b540b860d5f26ccf4afc3434831b8e9d8524`.
This revision adds no migration, dataset ownership, public tool, scheduler,
export, hosting, or deployment, and registry presence is not a live-run
receipt.
Registry `2.50.0` adds only the private
`fmp.market.iwm_etf_daily_history` collector and reciprocal bindings to the
four existing Stage 10 evidence, instrument, universe, and daily-price
datasets. It derives an exact 96-member `curated_etfs` successor from the
frozen 95-member snapshot plus IWM, then publishes one complete IWM history
through the existing replay-safe correction model. The collector is
`manual_only`, uses the existing FMP credential resolver and physical
market-store lock, performs no network work while that lock is held, and is
bounded to one single-attempt request, 30,000 rows, 16 MiB, and 45 seconds.
The inventory is 40 migrations, 53 datasets, 55 collectors, 8 jobs, 65
tools, 15 tool-version policies, 4 dashboards, and 1 export. The tool catalog
remains `2.11.0` with 54 contracts at SHA-256
`864a4d07afbf2558a331d30275cf21f4e31521142ee2d9d010dc26cc5680a757`;
the registry source SHA-256 is
`0adc78cbe419b18ece989c9cdd6d918ef13113fa5f573d6f5fbe045b9eca8259`.
Exact projection removes only this collector and its four reciprocal dataset
bindings and restores byte-exact registry `2.49.0` at SHA-256
`6d34dc495de10de42765e8909e30df744f69ad72d1901259f7da67be2d2710e1`.
It adds no migration, dataset, public tool, registry job, scheduler, export,
hosting, or deployment, and registry presence is not a live-run receipt.

Registry `2.51.0` adds only the private, manual-only
`fmp.market.iwm_etf_daily_history_backfill` collector and reciprocal bindings
to the existing Stage 10 evidence, instrument, universe, and price datasets.
It is fixed to the reviewed missing interval and one request; declaration is
not authority to run it. Exact projection restores byte-exact registry
`2.50.0` at SHA-256
`0adc78cbe419b18ece989c9cdd6d918ef13113fa5f573d6f5fbe045b9eca8259`.

Registry `2.52.0` adds only seven explicit, store-free analytical
`2.0.0` successors. The tool catalog advances to `2.12.0` at SHA-256
`3c15ebcbf145d188681a49016dc621ffc96a48f6480f7ff9067d962c8fa29693`;
exact projection restores registry `2.51.0` at SHA-256
`1d8485bd1df5351d94f0b13f2264828640d40c756c6241503f40a7b7f4e46c43`.

Registry `2.53.0` adds only
`company.search_filings@2.0.0`, a read-only exact-CIK filing search with
opaque keyset pagination bound to the query and optional cutoff. Catalog
`2.13.0` has SHA-256
`b313cc2c4e4fd39311c00a5c18ae3ef8aa4de157f58f23c0837b52a51d601ac5`;
exact projection restores registry `2.52.0` at SHA-256
`87c74bbc26ce6101ff6136eefe9fdab0d9616f06d714d049d4c30287eda78323`.

Registry `2.54.0` adds only explicit `2.0.0` successors for
`market.search_instruments` and `company.get_share_count_history`, plus the
market-instrument dataset's reciprocal tool binding. No logical tool name is
added. The current inventory is 40 migrations, 53 datasets, 56 collectors, 8
jobs, 65 tools, 25 version policies, 29 variants, 4 dashboards, and 1 export.
Catalog `2.14.0` has 74 contracts at SHA-256
`a70903e9ba65fd71d5d79775698174dafea630c58c435bb4d8fcc504320aa05b`;
the registry source SHA-256 is
`f40c4d2e0cad90f686bffe52116d138f3b578f33f4844245682387d398a19e01`.
Exact projection removes only those two policies and the reciprocal tool
binding and restores byte-exact registry `2.53.0` at SHA-256
`c201524e4e4a72b5377d36390e0cc5c746d392674b598ac1499ab818b418d238`.

Registry `2.55.0` adds only the explicit
`market.technical_indicators@2.1.0` variant. It retains v1 as the default,
preserves the exact v2.0 contract, opens no store, and adds no dataset or
collector binding. At that revision, the inventory was 40 migrations, 53
datasets, 56
collectors, 8 jobs, 65 tools, 25 version policies, 30 variants, 4 dashboards,
and 1 export. Catalog `2.15.0` has 76 contracts at SHA-256
`65311bb28efe62651ecb3420f0be13fd413df468487141cca0972d45e7684c85`;
the registry source SHA-256 is
`cec35d5cfed9f25c40f5adfa2f2581b0de8d442a75202e687f791b3a16dc3d7f`.
Exact projection removes only the v2.1 variant and restores byte-exact
registry `2.54.0` at SHA-256
`f40c4d2e0cad90f686bffe52116d138f3b578f33f4844245682387d398a19e01`
and catalog `2.14.0` at SHA-256
`a70903e9ba65fd71d5d79775698174dafea630c58c435bb4d8fcc504320aa05b`.

Registry `2.56.0` adds only the explicit
`market.technical_indicators@2.2.0` variant. It retains v1 as the default,
preserves the exact v2.0 and v2.1 contracts, opens no store, and adds no
dataset or collector binding. The current inventory is 40 migrations, 53
datasets, 56 collectors, 8 jobs, 65 tools, 25 version policies, 31 variants,
4 dashboards, and 1 export. Catalog `2.16.0` has 78 contracts at SHA-256
`cb1c6965582b90eaeec27054eda0d66e7a4c603aefa69f9b453ae342d779302b`;
the registry source SHA-256 is
`9782e77c530251401e9b5e42b33115949ce8bb28753f0a0471fa2e8353dd8802`.
Exact projection removes only the v2.2 variant and restores byte-exact
registry `2.55.0` and catalog `2.15.0`; their exact predecessor projection
then restores byte-exact registry `2.54.0` and catalog `2.14.0`.
These revisions add no migration, dataset ownership, provider authorization,
canonical write, registry job, scheduler, export, hosting, or deployment.

Registry `2.57.0` adds fifteen explicit `2.0.0` investment-analysis
successors while retaining every frozen v1 default and prior v2 contract. The
batch comprises query-bound pagination for
`macro.get_release_calendar`, explicit-symbol endpoint performance for
`market.cross_sectional_performance`, macro revision and surprise
standardization, direct funding/repo/curve readers, raw liquidity/credit/regime
and research-state readers, two retained Stage 11 energy readers, and the
normalized reviewed SEC fact reader `company.get_fundamentals`. It adds no
logical name: at that revision the inventory remained 65 logical tools with 40
version policies, 46 variants, and 108 catalog contracts. Its registry source SHA-256
is `1d36ddd20494cfc0ae9e6172f662c5dfe04b905319c0f83b439446f44fd29b5e`;
catalog `2.17.0` has SHA-256
`5c0ea96b9aba9d73f8f89aea20a052c858691e10a1047f22594f027d8f893101`.

The batch's exact predecessor projection removes only those fifteen explicit
v2 policies and restores registry `2.56.0`, catalog `2.16.0`, and registry
SHA-256
`9782e77c530251401e9b5e42b33115949ce8bb28753f0a0471fa2e8353dd8802`.
It adds no migration, dataset ownership, provider authorization, canonical
write, registry job, scheduler, export, hosting, or deployment.

Registry `2.58.0` adds only
`market.technical_indicators@2.3.0` with the Pine-equivalent KDJ calculation
over caller-supplied typed high, low, and close series. The unchanged 65
logical tools now have 40 version policies, 47 variants, and 110 catalog
contracts. Its registry SHA-256 is
`02a4aeb34632c5ae194622b7de77ec31145135bdf399620e45af461132134d43`;
catalog `2.18.0` has SHA-256
`4803071d5cb2ababa2710ac4c0e3eb1e1640b06aa0b6071e14c2f40be68fec72`.
Exact projection removes only v2.3 and restores byte-identical registry
`2.57.0` and catalog `2.17.0`. It adds no provider, credential, store,
migration, write, registry job, scheduler, export, hosting, or deployment.
Registry `2.59.0` adds five explicit variants across the unchanged 65 logical
tools: Williams Vix Fix v2.4, market breadth/risk v2.1, fixed-lag seasonality
for each retained energy reader v2.1, and normalized company ratios v2.1.
The current inventory has 40 version policies, 52 variants, and 120 catalog
contracts. Its registry SHA-256 is
`0457910706181d7f4d9bb07efbf18845dd86335999b55c5d5a8d203041d315a1`;
catalog `2.19.0` has SHA-256
`e4fdb9d9b6783ec131838762d8e2ae4b1c6833cc37c0292e1ceed09e0d919474`.
Exact projection removes only those five variants and restores byte-identical
registry `2.58.0` and catalog `2.18.0`. It adds no provider, credential,
store, migration, write, registry job, scheduler, export, hosting, or
deployment.

Registry `2.60.0` adds only
`market.technical_indicators@2.5.0` with the WaveTrend-with-crosses
calculation over caller-supplied typed high, low, and close series. The current
inventory has 40 version policies, 53 variants, and 122 catalog contracts. Its
registry SHA-256 is
`563c4294c47d534754014dc77a4a1b39e84749cc4d8165636bafd82feff679b0`;
catalog `2.20.0` has SHA-256
`cebc1ca58257fed167db36973be1ce37e6e01162c2bc072c24591a673b0aedb4`.
Exact projection removes only v2.5 and restores byte-identical registry
`2.59.0` and catalog `2.19.0`. It adds no provider, credential, store,
migration, write, registry job, scheduler, export, hosting, or deployment.

Registry `2.61.0` adds only
`market.technical_indicators@2.6.0` with the Parabolic SAR calculation over
caller-supplied typed high, low, and close series. The current inventory has
40 version policies, 54 variants, and 124 catalog contracts. Its registry
SHA-256 is
`0f43045c1dcc46b0f9d5ecb0aaf98aa3e9ad61a43e250afc389408602a8615ea`;
catalog `2.21.0` has SHA-256
`b70798594de6149b7710b36d3b735b3d24ff81eb23277ba54dfdc697aceb0efc`.
Exact projection removes only v2.6 and restores byte-identical registry
`2.60.0` and catalog `2.20.0`. It adds no provider, credential, store,
migration, write, registry job, scheduler, export, hosting, or deployment.

Registry `2.62.0` adds only the store-free
`market.technical_indicators@2.7.0` rolling-regression-line successor.  It
has 40 version policies, 55 variants, and catalog `2.22.0` with 126 contracts
at SHA-256
`18e86daf6ef3291407ab794d7f53015c80bb90c88f2518891f7b904002f515a1`; its
exact projection restores byte-identical `2.61.0`/`2.21.0`.

Registry `2.63.0` adds migration `news:0006_fmp_stock_latest_current`
(SHA-256 `bf8757bcc7679d1bd57408978eed996c9f4dad9c89339a52ca8adbeedbf83d30`),
private datasets `news.fmp.stock_latest_current_evidence` and
`news.fmp.stock_latest_current_articles`, manual-only collector
`fmp.news.stock_latest_current`, and `news.search@2.0.0`.  It has 65 logical
names, 41 version policies, 56 variants, and 128 catalog contracts.  Its exact
projection removes only this successor and reproduces registry `2.62.0` at
SHA-256 `59db17edd70d8ae9aa77f1468cf7c459338e3d1dff0015b3c99853b2e1e12fd2`
and catalog `2.22.0` exactly.  Registry declaration and offline validation do
not authorize a provider request, canonical application/population, or timer.

Registry `2.64.0` adds `news:0007_current_multi_source` (SHA-256
`df58936c73045ba8a382bf0a24743db5e314246e0f81d8943872b6d3e6c010c3`),
private datasets `news.current_multi_source_evidence` and
`news.current_multi_source_articles`, manual-only collector
`news.current_multi_source`, and `news.search@2.1.0`. It also adds
`news:0008_adopt_fmp_news_legacy` (SHA-256
`ea5302726758ab2bb987a525c3094885e016e4596689d26c8290808523f8327f`)
and inactive dataset `news.fmp.stock_latest_legacy_articles`. The latter
relation is schema-checked private evidence with no collector, tool, dashboard,
export, or source ID. The v2.1 source IDs remain the fixed FMP stock/press/
general, Federal Reserve, ECB, BEA, EIA, and Alpaca/Benzinga set; callers cannot
supply endpoints or database paths. Raw evidence and bodies remain private.

The bounded 2026-08-30 16:00 UTC batch completed all eight source steps and all
20 requests without retry, retaining 229 FMP-current and 1,069 generic-source
articles. On 2026-08-30 the reviewed hourly per-user timer was linked, enabled,
and started; it is active/waiting for its first normal `:10` UTC trigger, and
activation did not run the service or write a store. Exact
projection removes migrations 0007/0008, the three added datasets, the
collector, and v2.1, reproducing `2.63.0`/`2.23.0` byte-for-byte.



ADR 0012 defines the exact `2.40.0` topology. The legacy
`macro.fmp.economic_calendar_evidence` dataset remains active and readable
but has no live collector binding. The existing wholesale collector instead
outputs `macro.fmp.economic_calendar_incremental_evidence`, which owns the
receipt and singleton cache, and
`macro.fmp.economic_calendar_incremental_events`, which owns stable raw
events and append-only versions. Its persistence semantic identity includes
the predecessor receipt for a material transition in addition to the complete
source-batch identity, so exact replay is a no-write and A to B to A remains a
three-version history. The exact `2.40.0` to `2.39.0` projection restores the
legacy collector binding and full-capture mutation policy and removes only
migration 0018 and the two successor datasets.

The frozen Stage 10 projection remains `2.8.0`/`1.6.0`; the frozen Stage 9
projection remains `2.7.0`/`1.5.0`; the frozen Stage 7 projection remains
`2.5.0`/`1.3.0` with zero exports; the frozen Stage 6 projection remains
`2.4.0`/`1.2.0` with zero jobs; and the frozen Stage 5 projection remains
`2.3.0`/`1.1.0`. The Stage 8 declaration is a deliberate forward
reconstruction, not recovered 13-dataset Atlas parity.
Registry declaration and lifecycle metadata do not replace Stage 8 evidence:
its primary fixture gate and independent verification passed, and its bounded
exit gate was accepted on 2026-08-14 with an explicit waiver for unavailable
in-app browser automation. The waiver is not browser-pass evidence. The Stage
9 primary and independent offline gates and bounded live receipt passed. The
retained Stage 10 base and extension receipts are complete and independently
re-inspected. The bounded Stage 11 population and its private completion
receipt are complete. Registry declaration alone is not evidence of any live
result; the retained point-in-time receipts remain authoritative.

## Purpose

One versioned, machine-readable registry will be the declarative source of truth for:

- operational stores and their path overrides;
- ordered migration resources;
- dataset identity, store ownership, architectural layer, temporal policy, and freshness;
- collectors, semantic no-write gates, inputs, outputs, and scheduling jobs;
- physical write-lock and backup needs;
- read-only tool and dashboard exposure;
- derived materializations; and
- Atlas snapshot exports.

The registry prevents path, ownership, workload, and exposure policy from being copied independently into migrations, collectors, the scheduler, tool code, dashboard navigation, and the Atlas exporter.

## Boundaries and non-goals

The registry is declarative configuration, not:

- a database migration ledger;
- a replacement for each store's applied dataset registry or ingestion runs;
- a secret store or environment-file loader;
- a scheduler execution log;
- executable collector or SQL source;
- a place for raw provider payloads;
- an authorization mechanism for arbitrary SQL, tables, files, or database paths; or
- evidence that a declared component has been implemented or validated.

The registry names environment variables but never stores their values. Consumers receive an explicit environment mapping from the process. Registry loading does not search for or read a .env file.

## Canonical representation

The first implementation should use UTF-8 JSON because Python 3.11 can parse and validate it without a third-party dependency. The registry has one canonical file and one declared schema version. YAML in this document is illustrative only and must not be loaded, copied into production, or treated as a migration manifest.

Canonical serialization requirements:

- a top-level JSON object;
- no duplicate object keys;
- no comments, NaN, Infinity, or implementation-specific values;
- stable string identifiers and explicit arrays where order is meaningful;
- normalized forward-slash resource paths;
- semantic schema and registry versions;
- deterministic canonical serialization for hashing; and
- strict rejection of unsupported schema versions.

Object key order has no meaning. Migration arrays, declared dependency arrays where explicitly marked ordered, and presentation order arrays do have meaning.

## Registry object model

### Top-level document

| Field | Type | Required | Contract |
| --- | --- | --- | --- |
| schema_id | non-empty string | Yes | Identifies this registry schema, independent of a deployment |
| schema_version | semantic version | Yes | Selects validation rules; unsupported major versions fail closed |
| registry_version | semantic version | Yes | Changes whenever declarative content changes |
| status | enum | Yes | proposed, validated, or active |
| stores | array of Store | Yes | Exactly the four operational stores in this architecture |
| migrations | array of Migration | Yes | Ordered through explicit store/order fields, not object key order |
| datasets | array of Dataset | Yes | Dataset ownership and contracts |
| collectors | array of Collector | Yes | Manual or scheduled data-producing units |
| jobs | array of Job | Yes | Orchestration of collectors and exports |
| tools | array of ToolExposure | Yes | Fixed read-only tool contracts |
| dashboard | array of DashboardExposure | Yes | Fixed pages, views, and table/query allowlists |
| exports | array of Export | Yes | Derived analytical and Atlas snapshot outputs |
| presentation_order | object of ID arrays | No | Stable user-facing order only; never changes ownership semantics |

Every top-level collection is present, even when empty during a staged reconstruction. An active registry cannot omit required store, dataset, migration, or consumer coverage.

### Store

| Field | Type | Required | Contract |
| --- | --- | --- | --- |
| id | namespaced identifier | Yes | One of market, macro, company, news |
| default_path | safe relative path | Yes | Relative to an explicit project root; never absolute or traversing |
| path_env | environment-variable name | Yes | QUANT_MARKET_DB_PATH, QUANT_MACRO_DB_PATH, QUANT_COMPANY_DB_PATH, or QUANT_NEWS_DB_PATH |
| control_tables | array of identifiers | Yes | Includes schema_migrations, dataset_registry, and ingestion_runs |
| migration_order | array of migration IDs | Yes | Complete ordered list for this store |
| write_coordination | object | Yes | Declares physical_store lock scope, timeout, and deterministic ordering policy |
| backup | object | Yes | Declares SQLite online-backup behavior and verification requirements |
| legacy_compatibility | object | No | Must be absent from an active runtime registry; future one-shot import requires a separate contract |

Required default paths:

| Store | Default path | Override |
| --- | --- | --- |
| market | data/market.sqlite | QUANT_MARKET_DB_PATH |
| macro | data/macro.sqlite | QUANT_MACRO_DB_PATH |
| company | data/company.sqlite | QUANT_COMPANY_DB_PATH |
| news | data/news.sqlite | QUANT_NEWS_DB_PATH |

The current market default is fixed by ADR 0009. The superseded
`data/market_data.sqlite` name is not a fallback, alias, or second active
market store. An exact historical registry projection may restore that prior
declaration only to reproduce already accepted Stage 1–11 evidence; it cannot
be used as current runtime routing.

Likewise, `data/macro_data.sqlite`, `data/company_data.sqlite`, and
`data/news_data.sqlite` are historical registry declarations, not current
fallbacks or aliases.

The legacy `data/quant_data.sqlite` and `QUANT_DB_PATH` pair must not appear in
an active runtime registry. Unified runtime compatibility is retired; a future
one-shot import contract would be separate from Store declarations.

Store path resolution follows this precedence:

1. an explicit path supplied to a narrowly scoped command or test harness;
2. the supplied environment mapping at the store's path_env key; and
3. default_path relative to the supplied project root.

Every resolved path becomes absolute and normalized before opening a connection, deriving a lock, or grouping backups. Empty overrides fail rather than falling back silently.

### Migration

| Field | Type | Required | Contract |
| --- | --- | --- | --- |
| id | stable identifier | Yes | Unique across the registry; normally includes store scope and ordinal |
| store | Store ID | Yes | Store whose ledger receives the migration |
| ordinal | nonnegative integer | Yes | Total order within one store |
| resource | safe relative resource path | Yes | SQL resource; no absolute path or parent traversal |
| sha256 | lowercase hexadecimal digest | For active status | Hash of reviewed immutable bytes |
| semantic_scope | non-empty string | Yes | Human-auditable purpose, not a substitute for SQL |
| dependencies | array of migration IDs | Yes | Must be acyclic and refer to earlier compatible resources |
| reconstruction_state | enum | Yes | unresolved, fixture_validated, or recovered_exact |

Migration resource existence, checksum, declared order, ledger order, and dependency order are validated before a store is initialized or upgraded. A checksum change to an applied migration is an error. Corrections use a new ordinal.

The recovered semantic sequence extends through 0031. The exact SQL and checksums are not inferred from the semantic descriptions. Migration 0031 must preserve the filing/issuer derived-view contract and supporting invariants; it must not invent a physical join table.

### Dataset

| Field | Type | Required | Contract |
| --- | --- | --- | --- |
| id | namespaced identifier | Yes | Stable public identity, independent of a provider name |
| version | semantic version | Yes | Dataset contract version |
| store | Store ID | Yes | Exactly one operational owner |
| layer | enum | Yes | evidence, canonical, or derived |
| physical | object | Yes | Allowlisted table/view/materialization names and relation kind |
| identity | object | Yes | Natural keys, stable identifiers, and version identity |
| temporal | object | Yes | Observation fields, availability fields, precision, timezone rule, inclusive-range behavior, and supported vintage modes |
| revision_policy | enum/object | Yes | immutable_capture, append_version, current_state_capture, or derived_rebuild |
| freshness | Freshness | Yes | Expected cadence, stale threshold, and how freshness is measured |
| quality_contract | object | Yes | Missingness, units, validation rules, and required warnings |
| collector_ids | array of Collector IDs | Yes | Authorized producers; empty for purely derived/read-only inputs |
| tool_ids | array of Tool IDs | Yes | Tools allowed to consume or expose this dataset |
| dashboard_ids | array of Dashboard IDs | Yes | Dashboard exposures that may query it |
| export_ids | array of Export IDs | Yes | Declared analytical/Atlas outputs |
| active | boolean | Yes | Inactive datasets remain resolvable for migrations/history but are not collected |

The physical block may list several relations only when they all serve the dataset's declared layer. Relations serving different layers require separate dataset IDs and explicit lineage. Every named table or view belongs to exactly one dataset contract unless the registry explicitly marks a shared control-plane object.

The temporal block records, at minimum:

- observation/reference-period field and precision;
- available_at or local capture field and precision;
- source-native timezone/no-conversion rule;
- supported latest, as_of, and first_release modes;
- whether historical semantics are true source vintages or local-capture-only;
- inclusive start/end filtering; and
- explicit null/missing-reason behavior.

### Freshness

| Field | Type | Required | Contract |
| --- | --- | --- | --- |
| cadence | enum/object | Yes | intraday, daily, weekly, monthly, event_driven, manual, or an explicit calendar |
| expected_lag | duration | Yes | Expected source-to-local delay |
| stale_after | duration | Yes | Threshold after which health is stale |
| measured_from | enum | Yes | source_period, source_published_at, available_at, or successful_capture |
| calendar | identifier | No | Trading/release calendar when ordinary elapsed time is misleading |
| if_new | boolean | Yes | Whether semantic identity can produce a zero-write outcome |
| health_severity | enum | Yes | informational, warning, or critical |

Freshness never assumes that missing source coverage is a parser failure. Health distinguishes not due, unchanged, delayed source, partial ingestion, failed ingestion, and stale local coverage.

### Collector

| Field | Type | Required | Contract |
| --- | --- | --- | --- |
| id | namespaced identifier | Yes | Stable collector identity |
| version | semantic version | Yes | Behavior contract version |
| handler | implementation identifier | Yes | Resolved from trusted application code; not a shell fragment |
| input_datasets | array of Dataset IDs | Yes | Read dependencies, if any |
| output_datasets | non-empty array of Dataset IDs | Yes | Authorized write targets |
| semantic_identity | object | Yes | Scope-bearing comparison rule and fields intentionally excluded |
| mutation_policy | object | Yes | Append/upsert/version behavior and unchanged semantics |
| workload_bounds | object | Yes | Request, symbol, page, byte, row, and time bounds as applicable |
| retry_policy | object | Yes | Transient classes, attempt bounds, backoff, and Retry-After behavior |
| configuration_env | array of names | Yes | Names only; never values |
| physical_locks | derived marker | Yes | Must be derived from output store paths |
| schedule_eligibility | object | Yes | Manual-only or calendars on which jobs may invoke it |

For an if-new collector, semantic identity includes request scope and material parsed content. Fields known to vary without semantic change may be excluded only by an explicit, fixture-tested rule. A timeout, partial response, schema error, authentication failure, or rate limit cannot compare equal to a successful response.

Fetch happens before physical locks are acquired. The lock set is the set of canonical resolved output-store paths, sorted deterministically. Store-path validation rejects two logical stores that resolve to one physical file before collection can begin.

### Job

| Field | Type | Required | Contract |
| --- | --- | --- | --- |
| id | namespaced identifier | Yes | Scheduler/manual command identity |
| version | semantic version | Yes | Orchestration contract version |
| steps | ordered array | Yes | Collector or export IDs plus continue/fail policy |
| required_stores | derived marker | Yes | Derived from step dataset references, never copied as an independent list |
| calendar | object | Yes | Manual or explicit cadence/calendar gating |
| overlap_policy | enum | Yes | ignore_new or fail_new |
| aggregate_status | object | Yes | Defines success, incomplete, and nonzero behavior |
| receipt | object | Yes | External receipt/log behavior, including zero-write polls |

The registry describes jobs; a platform-specific scheduler definition is generated or validated against them. Scheduler XML or command lines are not embedded as authoritative business logic.

Schema `1.3.0` freezes eight ordered Stage 7 job declarations:
`news-hourly`, `sec-daily`, `options-close`, `macro-daily`,
`market-close`, `expectations`, `company-weekly`, and `macro-monthly`.
Every entry is `manual_fixture_only`, `scheduling_enabled: false`, uses
fixture-only network mode, derives its complete write-store set from steps,
declares bounded retry/timeout and dependency policy, publishes private
versioned receipts, and uses the registered aggregate exit precedence. Exact
external task definitions, timezone, identity, and calendar installation
remain unresolved; no scheduler definition is generated or installed.

Schema `1.5.0` adds exactly one live-enabled but manual-only collector:
`fmp.market.daily_price_backfill`. Its registered output datasets are
`market.fmp.daily_price_evidence`, `market.fmp.instruments`, and
`market.fmp.daily_prices`; they are isolated under
`market:0009_fmp_daily_price_backfill`. It has one request, 22-row,
262144-byte, and 30-second bounds, a no-retry policy, and a semantic
identity that excludes the credential and headers. `FMP_API_KEY` is an
environment-variable name only. There is no job, tool, dashboard, or Atlas
exposure for this collector or its datasets, and its declaration is not
evidence that a provider response was fetched.

Schema `1.6.0`, registry `2.8.0`, adds the bounded Stage 10 market-history
profile: `market:0010_stage10_market_history`, four private datasets, and the
`fmp.market.stage10_universe_capture` and
`fmp.market.stage10_daily_history` manual-only collectors. They use
`FMP_API_KEY` only as a configuration environment-variable name, use no
automatic retry, and have no public consumer. The retained base and extension
population results are recorded in [Stage 10 evidence](STAGE10_EVIDENCE.md).

Schema `1.7.0`, registry `2.9.0`, adds
`macro:0012_stage11_bea_eia_live_history`, six private evidence/canonical
datasets, and three manual-only collectors:
`bea.macro.stage11_nipa_history`,
`eia.macro.stage11_electricity_retail_history`, and
`eia.macro.stage11_petroleum_weekly_stock_history`. `BEA_API_KEY` and
`EIA_API_KEY` are environment-variable names only. The registry declaration
does not imply completion; the completed private candidate state is recorded in
[Stage 11 evidence](STAGE11_EVIDENCE.md).

Registry `2.10.0` keeps schema `1.7.0` and changes no migration, dataset,
collector identity, or public exposure. It canonically records the reviewed
Stage 11 transient retry policy: at most three physical attempts for
connection/timeouts, HTTP 429, or HTTP 500-599, with deterministic one-second
then two-second backoff and no jitter. Historical Stage 11 execution projects
back to the exact `2.9.0` declaration and source digest bound into the
completed receipt. This successor revision neither rewrites that receipt nor
authorizes a new live cohort.

Schema `1.8.0`, registry `2.11.0`, adds the bounded FMP stock-latest news
fixture contract: `news:0005_fmp_stock_latest`, two private news datasets, and
one manual-only collector. Its fixture evidence does not prove complete or live
news coverage and does not authorize another provider request.

Registry `2.12.0` keeps schema `1.8.0` and changes no migration, dataset,
collector, job, tool, dashboard, or export identity. It changes only the current
market default to `data/market.sqlite` under ADR 0009. The
`stage12_registry_profile` projection restores `data/market_data.sqlite` and
registry `2.11.0` before the existing Stage 11/10/earlier projections run.
That compatibility exists only for immutable historical evidence; the old path
is not current routing, a fallback, or an alias.

Registry `2.13.0` keeps schema `1.8.0` and adds the bounded Stage 12B fixture
collector only: `market.stage12b.fmp_daily_incremental_fixture`, handler
`market.stage12b_fmp_daily_incremental_fixture`, `manual_only` schedule
eligibility, one attempt, no transient retry classes or backoff, and finite
fixture bounds of 65,536 bytes, one request, five rows, and 30 seconds. It uses
the existing `0010` capture/version/current model only in explicit temporary
fixture stores and creates no migration, public exposure, or provider authority.
The exact `2.13.0` to `2.12.0` historical projection removes the Stage 12B
declaration and restores the accepted `2.12.0` registry before Stage 12A and
earlier evidence is reproduced. It is not a live, operational, or scheduler
authorization.

Registry `2.14.0` keeps schema `1.8.0` and adds only
`market.stage12c.fmp_daily_incremental_manual`. It creates no migration,
public exposure, or scheduler authority. The exact `2.14.0` to `2.13.0`
historical projection removes that declaration before Stage 12B and earlier
evidence is reproduced. The completed Stage 12C provider work remains bound
only to [its private receipt and evidence record](STAGE12C_EVIDENCE.md);
neither the current registry nor a historical projection alone proves a
provider call or database mutation. The completed Stage 12D no-transfer proof used the existing
`2.14.0`/schema `1.8.0` binding without repinning or modifying it; see its
[immutable evidence record](STAGE12D_EVIDENCE.md).

Registry `2.15.0` keeps schema `1.8.0` and changes only the current macro,
company, and news default basenames to `macro.sqlite`, `company.sqlite`, and
`news.sqlite`. The exact `2.15.0` to `2.14.0` projection restores the prior
declarations and exact Stage 12C/D registry source hash. No database file is
created, renamed, copied, opened, or mutated by this declaration change.

Registry `2.16.0` keeps schema `1.8.0` and adds only
`macro:0013_live_gdp_cpi_vintages`, `macro.official_vintages_evidence`,
`macro.official_vintages`, `bea.macro.live_gdp_vintages`, and
`bls.macro.live_cpi_vintages`. The model retains raw official evidence and
source-release identities without reinterpreting the Stage 11 candidate
relations. The exact historical projection restores `2.15.0`.

Registry `2.17.0` keeps schema `1.8.0` and adds only
`macro:0014_live_employment_vintages`,
`philadelphia_fed.macro.live_employment_vintages`, and
`bls.macro.live_employment_current`. It broadens the isolated official-vintage
model only for the two exact BLS employment series, retaining Philadelphia Fed
RTDSM source-vintage identities with local-capture availability and BLS current
facts in the same versioned lineage. The exact historical projection restores
byte-exact registry `2.16.0`.

Registry `2.18.0` keeps schema `1.8.0` and adds only
`macro:0015_live_macro_history_extension`,
`bls.macro.cpi_current_history`, and
`philadelphia_fed.macro.gdp_cpi_vintage_history`. It reuses the two private
official-vintage datasets. Both collectors are manual-only, one-attempt, and
credential-free; no new scheduler or public exposure is declared. Its exact
historical projection restores byte-exact registry `2.17.0`.

Registry `2.19.0` keeps schema `1.8.0` and adds only the manual-only
`fmp.macro.gdp_cpi_release_calendar_history` collector. It reuses
`fixture.macro.economic_calendar` and the existing `macro.official_vintages`
tool dependency, with no migration, dataset, job, scheduler, public consumer,
or physical surprise table. Its exact historical projection restores
byte-exact registry `2.18.0`.

#### 2026-08-13 Stage 11 runtime compatibility clarification

This explicit runtime override applied only to finish the then-started,
isolated non-production Stage 11 candidate. That candidate and the completed
Stage 10 cohort are pinned to canonical registry source SHA
`7e8ec6fc38d5a24962460d9c78a4df7754d3b29ad4b74c0c5e8ae807cd59ed56`.
Accordingly, neither the clarification nor registry `2.10.0` repins the
completed receipt, earlier BEA evidence, or earlier retail evidence, and
neither changes the Stage 9 or Stage 10 no-retry statements. The candidate is
complete and none of its provider requests may be repeated.

For that completed candidate only, each exact logical BEA request, EIA retail page, and
EIA weekly request is serialized. It may make one initial physical attempt
plus at most two automatic retries. Retrying is permitted only for
connection/timeouts, HTTP 429, and HTTP 500-599. Backoff is deterministic and
has no jitter: one second before the first retry and two seconds before the
second; every physical attempt observes at least one second of pacing. There
is no retry for authentication/authorization, non-429 4xx (including 408),
redirects, wrong MIME, `200` provider-error envelopes, byte/resource bounds,
or parser/schema/configuration/publication/database/integrity errors. No
database write lock or transaction may be held during calls or sleeps, and a
failed physical attempt creates neither evidence nor phase advancement. On
success, existing `*_request_count` fields count physical attempts, while
scope `max_requests` remains a count of logical units.

### ToolExposure

| Field | Type | Required | Contract |
| --- | --- | --- | --- |
| id | namespaced identifier | Yes | Stable public tool name |
| version | semantic version | Yes | Request/result contract version |
| handler | implementation identifier | Yes | Trusted read service |
| read_only | literal true | Yes | Any other value is invalid |
| datasets | array of Dataset IDs | Yes | Complete direct data dependencies |
| input_schema | strict JSON Schema object | Yes | Top-level and every nested object reject additional properties |
| output_schema | strict JSON Schema object | Yes | Finite RFC JSON plus audit envelope |
| examples | bounded array | Yes | Valid arguments with no paths or secrets |
| workload_bounds | object | Yes | Schema-visible input and output limits |
| availability_policy | object | Yes | Required cutoff/vintage behavior |

Tool schemas cannot contain arguments for a database path, SQLite URI, arbitrary SQL, unrestricted table name, filesystem root, or connection string. Dataset dependencies determine stores. The validator recursively checks property names, references, examples, and handler metadata.

The active parity target may claim 57 tools only after all 57 recovered names have strict, bounded input and output schemas and HTTP/in-process parity tests. Until then, the registry status remains proposed or validated rather than active.

### DashboardExposure

| Field | Type | Required | Contract |
| --- | --- | --- | --- |
| id | namespaced identifier | Yes | Stable page, panel, or inspector exposure |
| route | local route | Yes | Fixed application route |
| api_routes | array of local API routes | Yes | Fixed JSON endpoints owned by the exposure; methods remain enforced by the service boundary |
| datasets | array of Dataset IDs | Yes | Direct data dependencies |
| tools | array of Tool IDs | Yes | Guided tool forms available on the exposure |
| relations | array of allowlisted relation IDs | Yes | No caller-created table name |
| filters | strict schema | Yes | Bound fields and values |
| sort_fields | finite array | Yes | Server-owned allowlist |
| pagination | object | Yes | Default and hard maximum |
| visibility | enum | Yes | local_private |

Schema `1.2.0` introduced all ten DashboardExposure fields above. The
frozen Stage 6 `2.4.0` projection declares exactly four ordered local-private
exposures: `stage1.overview`, `stage6.gdp_vintages`,
`stage6.table_inspector`, and `stage6.agent_tools`. Canonical Stage 7
registry `2.5.0`/`1.3.0` preserves those declarations unchanged while
adding only the disabled job catalog. Canonical Stage 8 registry
`2.6.0`/`1.4.0` preserves both historical projections unchanged and adds
only the one bounded export declaration below. Canonical Stage 9 registry
`2.7.0`/`1.5.0` preserves all dashboard declarations unchanged; no FMP
daily-price dataset, relation, or collector appears in a dashboard exposure.
API routes remain fixed registry declarations, not caller-supplied URLs.

Dashboard navigation and the table inspector are generated or validated from
these exposures. Internal raw payload objects remain absent unless a reviewed
exposure explicitly permits them.

### Export

| Field | Type | Required | Contract |
| --- | --- | --- | --- |
| id | namespaced identifier | Yes | Stable export profile |
| version | semantic version | Yes | Output contract version |
| owner / description | strings | Yes | Accountable owner and bounded human-readable scope |
| semantic_dataset_id | namespaced identifier | Yes | Meaning identity, independent of one physical revision |
| kind | enum | Yes | analytical_file or atlas_snapshot |
| format | enum | Yes | json, jsonl, or optional parquet |
| lifecycle | object | Yes | Fixture/manual/network/hosting restrictions and declared lifecycle state |
| datasets | non-empty array of Dataset IDs | Yes | All exported datasets and their owners |
| query_contract | object | Yes | Columns, ordering, filters, caps, and point-in-time policy |
| schema_contract | object | Yes | Output schema version and null/number handling |
| chunking | object | Yes | Deterministic boundaries and maximum sizes |
| freshness | object | Yes | Snapshot health derived from dataset freshness |
| source_mode | enum | Yes | read_only_copy or online_backup |
| consistency | object | Yes | Cohort receipts and cross-store limitations |
| staging | object | Yes | Exact parent, generated child, validation, and atomic promotion |
| optional_dependency | string/null | Yes | Parquet adapter name when used; never silently required by the core |
| benchmark | object | Yes | Explicit format/adoption decision; no inferred package requirement |
| consumers | array | Yes | Fixed consumer IDs, modes, and hosting boundary |
| provenance | object | Yes | Required registry/schema/code/source evidence in the public manifest |

JSON and JSONL are supported by the standard-library core. Parquet is optional and must fail with a clear unavailable-capability error when its declared adapter is absent. Export format does not alter canonical storage.

An Atlas export reads only registered exportable datasets from explicit read-only store copies. Its manifest records registry version, dataset contract versions, per-store backup or read-copy receipts, row/chunk counts, schemas, freshness, hashes, generation time, and source revision. The exporter never labels a multi-store cohort as one atomic database timestamp.

#### Bounded Stage 8 declaration — accepted with browser-automation waiver

The current registry declares exactly one export:
`atlas.fixture_snapshot` version `1.0.0`, semantic dataset
`atlas.fixture_snapshot.core_v1`, owned by `quant_data.atlas`. It is an
`atlas_snapshot` in strict JSON, manual-only, fixture-only, with network and
hosting disabled. Its only consumer is `quant_data_atlas` in
`static_read_only` mode; it has no optional dependency. The explicit
benchmark decision is `not_required_json_atlas_snapshot`: Parquet and DuckDB
are both `not_adopted`.

Its exact reciprocal datasets are `fixture.market.daily_prices`,
`fixture.macro.gdp_vintages`, `fixture.company.issuers`, and
`fixture.news.items`. The four deterministic projections are
`market-prices` (5,000 rows), `gdp-vintages` (1,000),
`company-issuers` (1,000), and `news-items` (5,000), within a 12,000-row,
8 MiB, 30-second whole-snapshot limit. Ordered chunks are limited to 250 rows
and 262144 bytes; empty chunks are omitted.

The cutoff is the aware-UTC export-start instant. Availability is selected at
or before the cutoff, vintage mode is `as_of`, and source date-only precision
uses `completed_date`, never an invented timestamp. A complete deterministic
physical-lock cohort produces one SQLite online backup per store; the exporter
then reopens copies only with SQLite query-only access.

`receipt_chain: complete_baseline_plus_validated_deltas` permits a selected
validated partial correction only over a proved succeeded, validated, complete
baseline and a fully linked validated delta chain. Missing/invalid links,
selected running work, or an unreconciled later failure reject the export.
`partial_scope: forbidden` still requires all four registered projections; it
does not make a partial correction receipt complete on its own.

The export records per-store receipts and `cross_store_atomic: false`, stages
in an exact host-selected root, publishes an immutable revision, updates only
a `current` pointer atomically, and keeps receipts private. No public schema
may expose a database/filesystem path, credential, SQL, raw artifact, private
receipt, body, summary, source URL, run ID, artifact ID, or snapshot ID.

This declaration is not a claim of historical recovered parity, a live export,
or a hosting/deployment action. Its `fixture_validated` lifecycle declaration
does not itself close an acceptance gate. The primary and independent evidence,
plus the user's 2026-08-14 browser-automation waiver, close the bounded Stage 8
gate; see [Stage 8 evidence](STAGE8_EVIDENCE.md).

## Global invariants

Validation fails closed unless all of the following hold:

1. Every ID is non-empty, normalized, unique within its type, and stable under canonical serialization.
2. Every store, migration, dataset, collector, job, tool, dashboard, and export reference resolves.
3. Reference graphs are acyclic where cycles are prohibited; migration dependencies and job steps have deterministic order.
4. Exactly four operational stores exist with the required default paths and environment names.
5. Default paths and resource paths are relative, normalized, non-traversing, and contained by their designated roots after resolution.
6. Every dataset has one store owner, one supported layer, a declared physical relation set, temporal policy, quality contract, and freshness contract.
7. A physical table or view has one owning dataset unless explicitly declared as shared control-plane state.
8. Migration IDs and resources are unique within a store; ordinals are total and gap policy is explicit; active migrations have verified checksums.
9. Applied migration resources are never edited; ledger mismatch prevents writes.
10. Collector output references determine write authorization, required stores, physical locks, and backup boundaries.
11. Physical locks are keyed by canonical resolved database path. Logical aliases to one file cannot acquire independent locks.
12. If-new collectors define a scope-bearing, fixture-tested semantic identity and make zero persistent writes when unchanged.
13. Tools and dashboards are read-only, strict, bounded, and free of caller-selected SQL, relation, connection, or database-path arguments.
14. All date, timestamp, availability, vintage, missingness, unit, and transformation semantics are explicit.
15. Cross-store consumers declare one availability cutoff and return contributing store receipts.
16. Derived datasets and exports cannot be an undeclared source for canonical datasets.
17. Every current Atlas dataset declaration and the one registered export reference each other reciprocally; no fifth dataset or undeclared projection is exported.
18. Atlas profiles use explicit read-only/query-only online backups, stage inside an exact host-selected parent, validate the entire cohort, publish immutable revisions, and update only a current pointer atomically.
19. Public Atlas artifacts omit local paths, credentials, SQL, raw artifacts, private receipts, and other registered excluded fields; private attempt receipts never become a public artifact.
20. Unsupported layers, formats, schema versions, status values, and optional capabilities are rejected rather than guessed.
21. Secrets and secret values do not appear anywhere in the registry, examples, generated documentation, logs, or exported manifests.
22. Registry iteration and generated output are deterministic across processes and platforms.
23. Every network-capable collector is explicitly classified. A manual-only
    declaration grants no provider execution or recurrence; either action
    requires a separate explicit user decision and accepted operational
    contract or evidence. The registry never grants credential use, scheduler
    installation or invocation, public exposure, promotion, retirement, or
    permission to repeat completed work.

## Loading and validation

The loader is standard-library-only and accepts:

- an explicit registry path;
- an explicit project root;
- an explicit environment mapping; and
- an optional strictness/status requirement.

Precedence is explicit path, a named registry-path environment override if the application defines one, then the canonical project-relative path. An explicit empty value is an error. The loader never imports application handlers, opens databases, contacts the network, or reads a .env file.

Validation occurs in phases:

1. **Syntax:** UTF-8, valid JSON, no duplicate keys or non-finite values.
2. **Shape:** supported schema version, required keys, exact primitive/container types, and permitted enums.
3. **Identity:** non-empty normalized IDs, uniqueness, semantic versions, and stable names.
4. **References:** every foreign ID, dependency, exposure, and ordered step resolves.
5. **Paths:** safe defaults/resources, environment-name allowlist, normalized absolute resolution, and root containment.
6. **Ownership:** one dataset/store/layer owner, relation uniqueness, and producer authorization.
7. **Temporal and quality:** complete availability, vintage, precision, missingness, unit, and freshness contracts.
8. **Mutation and locks:** collector outputs, no-write semantics, canonical physical lock derivation, and deterministic acquisition order.
9. **Read exposure:** strict schemas, workload bounds, relation allowlists, no path/SQL arguments, and read_only=true.
10. **Export:** exportability, optional capability checks, snapshot-cohort rules, exact staging containment, and atomic-promotion contract.
11. **Runtime reconciliation:** when requested separately, compare the validated registry with migration ledgers, store-local dataset registries, and generated scheduler/dashboard/export artifacts.

Errors include a stable code and a JSON-pointer-like location. Validation reports all independent static errors in deterministic location order; runtime startup refuses write service if any fatal error remains.

## Consumers

| Consumer | Registry inputs | Required behavior |
| --- | --- | --- |
| Path resolver | Stores, defaults, environment names | Return normalized absolute paths; never implicit unified fallback |
| Migration runner | Store migration order, resources, hashes | Reconcile ledger and apply only reviewed forward migrations |
| Collector runner | Dataset ownership, semantic identity, bounds, outputs | Authorize writes and derive physical locks |
| Job/scheduler tooling | Ordered steps, calendars, overlap and status policy | Validate or generate platform wrapper; preserve aggregate failure |
| Health service | Store paths, datasets, freshness | Report not due, unchanged, delayed, partial, failed, and stale distinctly |
| Tool registry/API | Tool schemas, handlers, datasets, bounds | Expose only validated read-only contracts |
| Dashboard | Dashboard exposures, API routes, tools, relation allowlists | Generate or validate navigation/forms, bounded inspection, and fixed local routes |
| Atlas exporter | Export profiles, dataset ownership, store paths | Read copies, stage exactly, validate cohort, atomically promote |
| Documentation generator | Public metadata only | Produce deterministic reference pages without secrets or runtime claims |

## Generation policy

Generated artifacts may include:

- store/dataset ownership maps;
- migration plans;
- typed identifier constants;
- tool discovery documents and guided forms;
- dashboard navigation, API-route, and inspector allowlists;
- scheduler wrapper inputs;
- health/freshness catalogs;
- Atlas dataset and chunk manifests; and
- human-readable registry reference documentation.

Generation is one-way from a validated registry. Generated files carry the registry version and content hash and are never edited as an alternate source of truth. A check mode regenerates in memory and fails when a committed artifact differs.

Executable handler code, migration SQL, provider parsers, and domain validation are not generated from descriptive strings. The registry selects reviewed implementations; it does not synthesize them.

## Lifecycle and compatibility

1. **Propose:** add or change declarations with status proposed. Unknown checksums or unimplemented handlers are allowed only when explicitly marked and cannot run.
2. **Statically validate:** satisfy syntax, shape, ownership, references, paths, schemas, locks, and export rules.
3. **Fixture validate:** prove migrations, semantic identities, temporal behavior, bounds, failure states, and generated-artifact determinism against isolated temporary stores.
4. **Review:** approve contract versions, migration bytes/checksums, exposure, operational cadence, and compatibility behavior.
5. **Activate:** bump registry_version, set eligible objects active, generate/check derived artifacts, and deploy only after runtime reconciliation.
6. **Operate:** record registry version in migrations, ingestion outcomes, tool metadata, scheduler receipts, backups, and Atlas manifests.
7. **Evolve:** prefer additive fields and migrations. Breaking dataset/tool/output changes increment their major contract version.
8. **Deprecate:** mark an object deprecated with replacement and removal milestone; keep references resolvable until consumers migrate.
9. **Retire:** remove only after no active references remain and historical artifacts retain enough version metadata to be interpreted.

A schema-version change governs registry syntax and validation. A registry-version change governs one configuration revision. Dataset, collector, tool, job, and export versions govern their individual public or operational contracts.

Schema `1.9.0` retains `tools` as the ordered default-version inventory
and adds `tool_versions` plus `tool_version_schema_catalog`. Revisions
`2.32.0` through `2.40.0` contain the 57 recovered names; revision
`2.41.0` adds one native default-version declaration without changing that
frozen compatibility target. Each policy names
one existing logical tool, a default version, the fixed selector field, full
additional variant declarations, and deterministic deprecation records. A
variant is resolved by `(tool, version)`; duplicate pairs, undeclared schema or
operation versions, invalid replacements, non-reciprocal dataset bindings, and
unknown selectors fail validation. Historical projections strip this syntax
before applying earlier registry transforms.

## Illustrative YAML — non-executable

The following is deliberately incomplete YAML for discussion. It is **not the canonical format, not a valid migration manifest, and not executable configuration**. Placeholder values must not be guessed into an implementation.

~~~yaml
# NON-EXECUTABLE ILLUSTRATION ONLY
schema_id: quant-data-system-registry
schema_version: 1.0.0
registry_version: 0.1.0-proposed
status: proposed

stores:
  - id: market
    default_path: data/market.sqlite
    path_env: QUANT_MARKET_DB_PATH
    control_tables:
      - schema_migrations
      - dataset_registry
      - ingestion_runs
    migration_order:
      - market:<reviewed-migration-id>
    write_coordination:
      scope: physical_store
      key: derived_from_canonical_resolved_path

migrations:
  - id: market:<reviewed-migration-id>
    store: market
    ordinal: <reviewed-integer>
    resource: <reviewed-relative-sql-resource>
    sha256: <required-before-active>
    reconstruction_state: unresolved

datasets:
  - id: market.prices_daily
    version: 1.0.0
    store: market
    layer: canonical
    physical:
      relation: prices_daily
      kind: table
      version_relation: prices_daily_versions
    identity:
      natural_key: [instrument_id, trade_date, provider, price_variant]
    temporal:
      observation_field: trade_date
      observation_precision: date
      availability_field: fetched_at
      timezone_conversion: none
      range_semantics: inclusive
      vintage_modes: [latest, as_of]
    freshness:
      cadence: daily
      measured_from: successful_capture
      if_new: false
    collector_ids: [market.close]
    tool_ids: [market.get_returns]
    dashboard_ids: [tables.market_prices]
    export_ids: [atlas.market_prices]

collectors:
  - id: market.close
    version: 1.0.0
    handler: quant_data.collectors.market_close
    output_datasets: [market.prices_daily]
    semantic_identity:
      includes: [request_scope, parsed_observations]
    physical_locks: derived_from_output_store_paths
    workload_bounds:
      symbols: <reviewed-limit>

tools:
  - id: market.get_returns
    version: 1.0.0
    read_only: true
    datasets: [market.prices_daily]
    input_schema:
      type: object
      additionalProperties: false
      properties: <complete-reviewed-schema>
    output_schema: <complete-reviewed-strict-schema>
    workload_bounds: <reviewed-bounds>

dashboard:
  - id: tables.market_prices
    route: /table-inspector
    api_routes: [/api/table-inspector]
    datasets: [market.prices_daily]
    relations: [prices_daily]
    filters: <strict-bounded-schema>
    sort_fields: [trade_date, instrument_id]
    visibility: local_private

exports:
  - id: atlas.market_prices
    version: 1.0.0
    kind: atlas_snapshot
    format: json
    datasets: [market.prices_daily]
    source_mode: read_only_copy
    consistency:
      cohort_receipts: required
      claim_atomic_cross_store_as_of: false
    staging:
      exact_generated_child_under_designated_parent: true
      atomic_promotion: true
~~~

## Acceptance criteria

### Static registry

- Python 3.11 standard library can load the canonical JSON without importing the application.
- Duplicate keys, unknown schema versions, empty IDs, duplicate IDs, wrong types, unsupported enums, and non-finite values fail with stable locations.
- All references resolve and iteration/generation order is deterministic.
- The four store IDs, exact default paths, and exact environment override names match this specification.
- Unsafe, absolute, traversing, empty, and root-escaping defaults/resources fail.
- No registry consumer reads .env implicitly.

### Stores, datasets, and migrations

- Every dataset resolves to exactly one store and one of the three layers.
- Every physical relation has one declared dataset owner or an explicit shared-control designation.
- All four stores initialize independently in temporary paths; tests cannot reach defaults or live paths.
- Per-store migration order is total, resources are unique, active checksums match, and applied bytes cannot change.
- Reconstructed migrations through the recovered 0031 semantic boundary have fixture evidence before parity is claimed.
- Store-local migration and dataset state reconcile with the registry.

### Collectors, jobs, and locks

- Collector outputs derive required stores, authorized writes, physical locks, and backup boundaries.
- Resolved physical-path collisions fail configuration validation before a lock or backup is requested.
- Lock acquisition order is deterministic.
- Fetches and retries occur outside write transactions.
- Each if-new semantic identity has changed, unchanged, scope-changed, partial, error, timeout, and rate-limit fixtures.
- Unchanged produces zero ingestion-run, artifact, snapshot, membership, and canonical writes.
- The Stage 9 FMP collector is fixture-tested only for `SPY`, inclusive
  `2026-07-01` through `2026-07-31`; wrong scope, malformed response, missing
  credential, retry, or public-exposure attempts fail closed without a live write.
- Independent job-step failures remain visible and required failures produce an incomplete/nonzero aggregate result.

### Time and data quality

- Date-only and offset-aware values round-trip without fabricated timezone or precision.
- Inclusive range tests cover monthly observations over a calendar-year boundary.
- Latest, as_of, first_release, and unsupported-vintage behavior have explicit fixtures.
- Missingness, units, scale, price variants, identifiers, currencies, and option captures cannot be silently mixed or filled.

### Tools and dashboard

- Every exposed tool is read-only, deterministic, strict at every object schema, and workload-bounded.
- No input or example accepts arbitrary SQL, a connection string, a database path, or an unrestricted relation.
- Dataset dependencies determine store routing.
- HTTP and in-process validation and results match before parity is claimed.
- Dashboard routes, forms, sort fields, filters, relation allowlists, and pagination reconcile with registered exposures.

### Atlas and analytical exports

- Export profiles resolve only declared datasets and explicit owner stores.
- JSON/JSONL work without optional dependencies; unavailable Parquet fails clearly and cannot change canonical state.
- Export reads read-only copies or online backups, never live writable handles.
- One cohort manifest records registry/dataset versions, per-store receipts, schema, hashes, counts, chunks, freshness, and generation time.
- Exact staging containment, full validation, semantic-change detection, and atomic promotion have destructive-path and failure-injection tests.
- Cross-store output never claims an atomic timestamp it cannot prove.
- Failed publication leaves the last validated Atlas snapshot unchanged.

## Questions to resolve before activation

These are activation blockers, not invitations to invent missing facts:

- the canonical registry filename and registry-path environment variable;
- byte-exact migration resources and checksums;
- the complete final input/output schemas and workload bounds for all recovered tools;
- exact scheduler wrapper artifacts and final calendars;
- the Atlas source/package lock and deployment receipt format; and
- which optional derived materializations, if any, warrant persistent tables rather than ephemeral results.
