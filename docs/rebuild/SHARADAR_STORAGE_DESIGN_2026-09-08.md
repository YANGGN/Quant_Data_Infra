**Sharadar storage and coverage design**

Status: Proposed design; includes the user-selected universe and local list comparison.
Decision date: September 8, 2026, America/Toronto.
Sources reviewed September 9, 2026 UTC.
Scope: Nasdaq-delivered Sharadar fundamentals and a coverage map for related products.

The [shared-universe implementation plan](COMPANY_UNIVERSE_EXPANSION_PLAN_2026-09-08.md)
covers the selected 2,248 equities across Sharadar, daily prices, SEC filings and
CompanyFacts, dividends/splits/earnings dates, full FMP statements and estimates,
and Equibles transcripts using the reported 100,000-call/day paid allowance.
It also includes expanded news matching/batching and a separate review of other
FMP inputs. Price collection retains existing ETF/index selections; options and
macro keep independent scopes. That plan owns cross-provider rollout and
checkpoint/quota changes; this document owns Sharadar source-data semantics.

The recommendation is to preserve every supplied reporting dimension, every
source field, and every changed provider row that we subsequently observe.
Fundamentals belong in the existing company SQLite store. Prices and
instrument reference data retain market ownership. Provider-specific facts
remain distinguishable from FMP and SEC observations.

This proposal does not allocate a migration, change provider priority, or
activate collection. The only authenticated Sharadar evidence available from this task
is the earlier one-request AAPL SF1/ARQ check: six rows, 112 response columns,
HTTP 200, and no retained raw-response file or canonical publication. That
check established the requested AAPL access, not full-universe or bundle
entitlements.

**1. What must be preserved**

Three histories answer different questions:

| History | What it represents | Preservation rule |
| --- | --- | --- |
| Reporting observations | ARQ, ARY and ART observations associated with filings | Retain every distinct source observation, including multiple filings for a period |
| Restated period views | MRQ, MRY and MRT values as currently supplied for prior periods | Retain each changed MR row we observe; never overwrite an earlier capture |
| Provider corrections | Changes to either AR or MR rows, definitions, identities or metadata | Append local versions with exact evidence and observation time |

Sharadar describes AR as filing-indexed and MR as period-indexed. AR can have
multiple observations in a quarter and missing quarters when filings arrive
late. Quarterly dimensions are not generally available for foreign issuers.
Its methodology can allocate an annual restatement to the final MRQ quarter
when earlier quarterlies were not restated. These are source semantics to
preserve, not errors to repair automatically.
[Reporting dimensions and methodology](https://sharadar.com/docs/fundamentals)

A download today does not establish every MR state that existed before today.
Likewise, comparing today's AR and MR rows does not establish the dates of all
intermediate restatements. Our archive will preserve all changes actually
observed after collection begins. A state that appears and disappears between
two acquisitions can still be missed. Recovering a complete earlier
restatement sequence would require additional versioned vendor evidence or
filing-level reconstruction; it cannot be synthesized from SF1 alone.

**2. Delivery channels and coverage**

Use Nasdaq Data Link as the initial delivery channel because that is where the
user's key was tested. Sharadar launched a separate direct service in July
2026. Its documentation is useful for current product semantics, but endpoint
names, credentials, filters and schemas must remain channel-specific.
[Sharadar's direct-service announcement](https://blog.sharadar.com/2026/07/sharadar-launches-direct.html?m=1)

The following is the desired coverage inventory, not a claim that the account
has access to every table. Codes shown as aliases in direct documentation must
be checked against Nasdaq metadata before implementing those adapters.

| Family / Nasdaq code or candidate alias | Data to retain | Proposed owner | Coverage note and source |
| --- | --- | --- | --- |
| SHARADAR/SF1 | Every column, all six dimensions, every returned period | Company | Primary common-stock fundamentals, active and delisted; history varies by issuer. [Nasdaq SF1](https://data.nasdaq.com/databases/SF1) |
| SHARADAR/TICKERS | Complete metadata rows, permanent identifiers, alternate labels and reference changes | Market for security-reference scopes; Company for insider/investor-reference scopes | Register disjoint table-filter scopes; do not require an existing local universe match. [Tickers](https://sharadar.com/docs/tickers) |
| SHARADAR/INDICATORS | Descriptions, units, definitions and table-specific metadata | Each consuming owner retains its own table-specific definition snapshots | Direct service calls this descriptions. [Indicator descriptions](https://sharadar.com/docs/descriptions) |
| SHARADAR/DAILY | All daily valuation columns and changed historical rows | Company | Complements periodic fundamentals; source documentation specifies millions for daily market cap and EV. [Daily fundamentals](https://sharadar.com/docs/daily) |
| SHARADAR/ACTIONS | All action types, counterparties, values and changes | Company | Includes distributions, splits, mergers, listing changes and other action types. [Actions](https://sharadar.com/docs/actions) |
| SHARADAR/EVENTS | All material-event codes and source rows | Company | Form 8-K event metadata; not a replacement for full filing documents. [Events](https://sharadar.com/docs/events) |
| SHARADAR/SP500 | Current membership, change events and supplied historical snapshots | Market | Preserve effective dates and acquisition dates separately. [Index constituents](https://sharadar.com/docs/sp500) |
| SEP / stocks | All source OHLCV, adjusted and unadjusted close fields and corrections | Market | Includes additional security classes beyond SF1. [Stock prices](https://sharadar.com/docs/stocks) |
| SFP / funds | All fund price fields and corrections | Market | Covers ETFs, CEFs, ETNs and exchange-traded debt; this is price coverage, not fund holdings. [Fund prices](https://sharadar.com/docs/funds) |
| SF2 / insiders | Complete insider holding/transaction records, security types and filing metadata | Company | Forms 3, 4 and 5; direct documentation lists history from January 2008. [Insiders](https://sharadar.com/docs/insiders) |
| SF3 / holdings | Investor-security-period positions, security type, value and units | Company | Form 13F holdings; direct documentation lists history from June 2013. [Institutional holdings](https://sharadar.com/docs/holdings) |
| SF3A / holdings_ticker | All supplied aggregates by security, including each security-type breakdown | Company | Retain separately from locally calculated aggregates. [Summary by ticker](https://sharadar.com/docs/holdings-ticker) |
| SF3B / holdings_investor | All supplied investor-level aggregates | Company | Retain investor identity independently of issuer identity. [Summary by investor](https://sharadar.com/docs/holdings-investor) |
| Direct metrics; Nasdaq equivalent unconfirmed | Current price, moving averages, beta, returns, yields and volume measures | Market | Documented as a current snapshot. Archive prospectively; do not promise a historical snapshot archive from its date-range label. [Price-based metrics](https://sharadar.com/docs/metrics) |

The design covers 14 documented table families, including the additional direct
metrics family. It does not infer additional tables from a marketing bundle
name. Nasdaq metadata and actual entitlements determine the executable
inventory. No direct-service request or credential substitution is proposed
without an explicit channel decision.

The user's subsequent scoped decision replaces the full-provider-universe
proposal: use Major Index Liquid_2026-09-08.csv as the initial selection. Its
2,248 unique, nonblank source tickers are retained without filtering or symbol
rewriting. Preserve complete available history, all source fields and all six
SF1 dimensions for resolved subjects within that selection. The broad product
inventory above is a coverage map, not authorization to acquire every symbol
or every table. Table-specific metadata and related entities must be explicitly
bounded in the eventual acquisition manifest.

The immutable comparison found 519 existing FMP equity instruments: 514 exact
source-symbol matches, two punctuation-alias candidates (BRK.B/BRK-B and
BF.B/BF-B), and 1,732 potential additions after those two mappings are verified.
AVB, EA and EQR are absent after considering those candidates; retain their
instrument records and historical facts. Sharadar identity and actual SF1
coverage remain unchecked for the selected list.

Store this as the named major_index_liquid universe with immutable membership
snapshots in the market domain, sharing resolved instrument identities across
universes. The current stage10 instrument relation is FMP-specific; its universe
IDs are closed and snapshots are capped at 800 members. A future additive
generic universe/snapshot/member model should bridge existing identities.
Do not append the CSV to stage10_instruments as a proxy for membership: some
existing collectors scan all equities and could thereby expand their workload.
Pin collection to the selected snapshot and resolve that dependency before a
future import. No schema or recurring-unit change is part of this list task.

The supplied CSV is a user-selected watchlist, not evidence of exact four-index
membership. Its filename date is a source label, not a verified historical
effective or availability timestamp. Source membership and later provider
identity assertions remain separately versioned. The retained source, complete
ticker list and [comparison/storage note](../../.local/sharadar-universe-20260908/selected-universe/README.md)
record provenance and pending mappings.

Coverage is measured per provider subject, dimension and period; advertised
start dates are not guaranteed for each company. Annual and trailing
observations remain useful when quarterly data is not supplied. Leaving the
selected universe never deletes previously captured facts or evidence.

**3. Where the data lives**

Follow the accepted [four-store architecture](../adr/0001-four-operational-sqlite-stores.md).
The company default remains data/company.sqlite and the market default remains
data/market.sqlite, resolved through the existing registry and store map.

Company owns SF1, DAILY, corporate actions, filing-event metadata, insiders and
institutional ownership. Market owns prices, index membership, the provider
security master and price-based snapshot metrics. There is no new provider
database.

Each dataset has one owning store. Table-specific evidence and canonical
datasets are registered separately. TICKERS is explicitly partitioned by its
source table discriminator: market owns security-reference scopes and company
owns insider/investor-reference scopes. INDICATORS is partitioned by the table
its definitions describe. Requests and manifests preserve these disjoint
scopes; a mixed-scope response is not silently duplicated across stores.
Company retains SF1 definitions, market retains SEP definitions, and so on.
These are not competing copies of a single mutable metadata authority.

Company keeps immutable mapping assertions that reference the market provider
subject and source evidence. It does not duplicate or take ownership of the
market security master. Cross-store links use application IDs and evidence
references, not foreign keys between SQLite files. A failed market/company
pairing remains explicit; publication is not described as globally atomic.

Existing FMP and SEC tables and source versions remain independent. Selecting a
preferred source later is a separately versioned research/read policy.
Disagreement never causes one provider's evidence to be overwritten by another.

**4. Concrete company storage model**

Use a relational identity/time/lineage envelope around a complete, lossless
row payload. Each SF1 version contains a full row. Avoid making hundreds of
individual metric rows the storage unit: a broad universe would multiply row
and index counts without helping preserve statement coherence.

The following names and fields are proposed logical contracts, not executable
DDL or registered relations.

| Relation | Essential contents |
| --- | --- |
| company_sharadar_artifacts | Content-addressed response bytes, compression/media information, byte hashes, safe source locator, sanitized request scope |
| company_sharadar_captures | Immutable acquisition scope, table/channel, capture time, provider snapshot label if supplied, schema reference, completeness and semantic transition identity |
| company_sharadar_capture_artifacts | Page/file membership, ordinal and content identity |
| company_sharadar_schema_versions | Table schema, primary-key fields, definition snapshot, filter capabilities, semantic contract version |
| company_sharadar_identity_assertions | Versioned source-key-to-provider-subject and provider-subject-to-issuer/instrument links, evidence and confidence |
| company_sharadar_sf1_observations | Stable source observation ID, channel/table namespace and typed source-key tuple |
| company_sharadar_sf1_versions | Immutable complete values, source dates, metadata, version sequence, predecessor, evidence and local availability |
| company_sharadar_sf1_membership | Capture, observation, selected version and exact source-row pointer |
| company_sharadar_sf1_heads | Current accepted version for each source observation; a rebuildable convenience pointer |
| company_sharadar_quality_findings | Version-linked validation findings, identity ambiguity and exclusions; append-only resolutions |
| company_sharadar_dataset_checkpoints | Existing control-plane checkpoint binding for successfully published bounded partitions; no progress past incomplete required partitions |

Reuse existing ingestion_runs and registry machinery rather than introducing
another run ledger. Other company table families use the same acquisition
pattern but their own domain-specific keys and version relations. SF2 and SF3
are not squeezed into the SF1 schema.

For an SF1 version, retain:

~~~text
version_id, observation_id, version_sequence, supersedes_version_id
capture_id, artifact_id, source_row_pointer, schema_version_id
source_ticker, dimension
source_datekey, reportperiod, calendardate, fiscalperiod_raw_if_supplied
source_lastupdated
captured_at, ingested_at
available_at, available_precision, availability_basis
values_json, missingness_json, unrecognized_fields_json
value_hash, row_semantic_hash, change_kind, quality_state
~~~

Keep annual, quarterly and trailing observations separate even when they have
the same report period end. Do not infer an exact period start by subtracting
three or twelve calendar months; preserve an absent source start as unknown.

The identity tuple must come from pinned Nasdaq table metadata. For SF1 the
design must accommodate ticker, dimension, datekey and reportperiod, rather
than only ticker and calendardate. The direct documentation names its date-key
field date, whereas the actual Nasdaq response used datekey. A tested adapter
maps those names only within its own channel.
[Fundamentals schema](https://sharadar.com/docs/fundamentals),
[Nasdaq key and filter discovery](https://docs.data.nasdaq.com/docs/parameters-1)

Persist a canonical typed source_key_json and its hash. A proposed key that
produces duplicate conflicting rows fails validation. Do not silently keep the
last row or enlarge the key with arbitrary values to hide an unresolved
identity problem. A provider key change creates a new schema contract and an
explicit identity reconciliation; it cannot mutate old primary keys.

Store foreign identifiers as text. The provider subject is namespaced by
Sharadar identity and, where applicable, entity kind. A permanent ticker is
not automatically a CIK. Canonical issuer mapping can remain unresolved while
source-native data is retained and queryable by provider identity. This allows
wide coverage without inventing companies or dropping foreign/delisted rows.

A source filing date is not an SEC accession number. Any link to an existing
SEC filing needs confirmed identity and filing evidence, not a ticker/date
join alone.

Ticker renaming or reuse does not authorize rewriting old evidence. Preserve
source keys and add dated, evidenced identity assertions. When multiple source
keys map to one subject, canonical readers reconcile only equivalent,
compatible observations; conflicting values remain a reported conflict.
Sharadar explicitly rewrites ticker labels in historical data and provides
permaticker for continuity.
[Ticker-history rules](https://sharadar.com/docs/faqs)

**5. Preserve every field without losing numeric meaning**

The initial adapter retains all columns returned by the actual Nasdaq schema,
not a hardcoded list of 150 based on the product description. The AAPL check
returned 112 columns. A column's position in a response is never its identity.

values_json contains the complete validated field set using canonical decimal
strings for numeric values, JSON booleans where appropriate, and explicit
nulls. Python Decimal parses numeric input before any binary-float conversion.
Source bytes retain the original numeric lexemes. The schema snapshot
distinguishes a decimal string from a source text field.

Readers expose typed row objects and curated wide SQL views for common
financial fields. Exact monetary calculations use Decimal. A REAL projection
used for an approximate analytical scan is explicitly derived; it does not
replace the authoritative decimal value. Frequently used date/entity filters
have ordinary B-tree indexes. Add metric indexes only for a demonstrated query.

Preserve income and expense components; balance sheet and working-capital
components; financing, investing and operating cash flows; share measures;
dividends; profitability and valuation ratios; currency conversions; and all
source metadata. These are coverage categories, not a field allowlist.

Each definition version records source field, type, unit, scale, currency
basis, adjustment basis and interpretation. Missing source null, absent column,
not-applicable, unresolved unit and failed parsing are distinct states. Unknown
columns are retained for review but not silently admitted as supported analytic
metrics. A dropped column is not a new zero. Current ticker metadata does not prove a
historical currency or unit segment. Keep that basis unknown unless the row
or compatible dated evidence establishes it; retain source USD equivalents
as distinct fields when supplied.

For example, the direct documentation describes SF1 marketcap/ev in USD while
DAILY uses USD millions. SF3 position values use USD millions and units use
thousands. Currency amounts, USD equivalents, ratios and percentages must
therefore be interpreted per table and schema version. Pin Nasdaq definitions
before performing these conversions.
[Daily units](https://sharadar.com/docs/daily),
[Holdings units](https://sharadar.com/docs/holdings)

Do not sum balance-sheet snapshots across quarters, confuse basic shares with
weighted-average shares, or silently recompute provider ratios. If a derived
calculation is useful, retain its formula and source-version IDs separately.
Nasdaq's SF1 methodology also warns that ART need not equal the sum of four ARQ
values, because a later filing may revise comparative periods used in its
trailing calculation.
[Nasdaq SF1 methodology](https://data.nasdaq.com/databases/SF1)

**6. Time and query contracts**

Use the existing [data/time contract](DATA_AND_TIME_CONTRACTS.md) and
[availability ADR](../adr/0004-point-in-time-availability-model.md).

| Field | Meaning in this design |
| --- | --- |
| reportperiod | Actual source financial period end |
| calendardate | Provider's standardized calendar-period label |
| source_datekey | Dimension-specific source index: filing-associated for AR; period-associated for MR |
| source_lastupdated | Provider's row-update date; discovery metadata, not an exact revision timestamp |
| captured_at | Offset-aware instant this installation obtained the complete evidence |
| ingested_at | Offset-aware local commit/audit time |
| available_at and basis | Defensible usability boundary for this exact stored version under its declared mode |

A fiscal date can differ from a calendar label: the pilot's most recent row
had reportperiod 2026-06-27 and calendardate 2026-06-30, with datekey
2026-07-31. Preserve all three. The raw pilot bytes were not retained, so this
is a conversation observation and cannot become a fabricated historical
artifact or seed publication.

Date-only source fields stay dates. A timestamp cutoff uses the project's
completed_date policy against a date-only filing boundary: same-date
intraday availability is not established. Never invent a midnight release
time. A later source_lastupdated also does not tell us when each field changed
or what its preceding value was.

Readers expose explicitly different questions:

| Query | Selection / limits |
| --- | --- |
| latest | Latest retained version of the requested dimension and source observation; freshness is reported separately |
| local_capture_as_of | Version established by an accepted capture on/before the knowledge cutoff; rejects earlier unobserved history |
| provider_as_reported | AR-only reconstruction filtered by provider filing date, with a separately pinned local archive/knowledge cutoff |
| strict as_of | Requires defensible availability for the exact version and every selected field; otherwise uses an explicitly selected local-capture basis or reports unavailable |
| revision_history | All observed versions and original source observations, with capture-time ordering and known gaps |
| first_release | Supported only with affirmative release-completeness evidence; the earliest downloaded AR row alone is insufficient |

provider_as_reported reports that it is a vendor-supplied historical
reconstruction, not proof of the exact vendor database state at that time.
This distinction preserves the useful historical AR series while avoiding a
stronger claim than the evidence supports. Provider corrections and
adjustment-sensitive fields require their own temporal qualification.
MR rows are not backdated to reportperiod for strict as_of queries.

The canonical version's default availability uses local capture until stronger
source-version evidence is established. A provider_as_reported policy uses
the source filing date explicitly and returns its distinct availability basis;
it does not rewrite the canonical availability field.

A knowledge cutoff pins which captured versions of the vendor reconstruction
are eligible. Pin schema and identity assertion versions too. Adding later
captures cannot change an already pinned result. To reproduce what had
actually been committed and queryable by a past wall-clock instant, additionally
filter by ingested_at; this is different from possession at captured_at.

**7. Restatement comparison and an example**

The following amounts and dates are synthetic:

| Observation | Value | Source date | Local capture | Interpretation |
| --- | ---: | --- | --- | --- |
| ARQ, period P, filing F1 | 100 | F1 | T1 | Provider's original reported-period observation |
| MRQ, period P, version 1 | 100 | P | T1 | Restated-period view first observed locally |
| MRQ, period P, version 2 | 95 | P | T2 | Later restated/corrected period view observed locally |

Both MR versions survive. A T1 local-capture query returns 100, and a T2 query
returns 95. The AR observation stays separately available. If MR later returns
to 100, that is a third transition with a new predecessor; it must not disappear
because the value 100 was seen before.

A comparison matches stable subject, actual report period, frequency,
currency, units, definition version and compatible adjustment basis. It must
also specify which AR filing observation is the baseline. Never compare an
annual figure to Q4 merely because both end in September.

The result is an observed difference, with field-level before/after values
and evidence IDs. Label it company_restatement only with evidence supporting
that cause. Other categories include provider_correction, metadata_change,
identity_change and unknown. Values alone cannot establish the cause.

SF1 also includes market-sensitive fields. AR/MR price or valuation differences
may arise from different price dates, not accounting restatements. Exclude
those comparisons unless their measurement basis is proven compatible.
Quarter balancing and trailing-period calculations retain methodology
warnings rather than being automatically corrected.

**8. Acquisition, versioning and publication**

The future collector should use a metadata manifest followed by a full
historical baseline and bounded incremental acquisitions. This document
proposes the mechanisms, not a live workload or scheduled unit.

1. Discover the exact entitled Nasdaq table inventory, metadata, primary keys,
   filters and indicators. Save a reviewed manifest with channel-specific
   definitions and no credential values.
2. Freeze the selected universe snapshot, evidenced provider-symbol mappings,
   and explicit table/date/dimension scope of the initial baseline. Retain all source fields. Use bulk export for large partitions
   where supported; avoid thousands of tiny per-symbol calls.
3. Keep downloads, archive validation, parsing and completeness checks outside
   database write transactions. Record each page/file's original membership.
4. Compare semantic state before creating canonical evidence. An unchanged
   acquisition or exact replay writes zero canonical runs, artifacts, captures,
   versions, memberships and checkpoints.
5. For changed state, reuse equal row versions and append only changed/new row
   versions. Publish evidence, membership, versions and the successful bounded
   checkpoint in one store-local transaction under the physical-store lock.
6. Use bounded complete partitions for large tables. Partition publication can
   be atomic even when the whole baseline spans transactions. A baseline-ready
   manifest is published only when all required partitions are complete.
7. Expose partial progress explicitly. A capped page sequence, parse rejection,
   stale export or failed partition never claims complete coverage.

Semantic identity includes table/channel, meaning-bearing scope, definitions,
completeness and normalized contents. Local fetch times, pagination tokens,
wire ordering and compression are not changes to financial meaning. A changed
lastupdated value is retained as metadata change even when financial values
are equal; value_hash and row_semantic_hash distinguish the two.

Use transition identity and exact acquisition replay identity separately.
Replaying an accepted retained acquisition is always a no-write. A fresh
A-to-B-to-A sequence must preserve all transitions. Do not use a global unique
constraint on (observation_id, value_hash) or an any-historical-payload no-write
test that would suppress a real reversion. No-write and supersession decisions
are rechecked under the lock before commit.

For changed data, retain exact credential-free data-response bytes as compressed
SQLite BLOBs, with hashes of the original bytes and compression metadata.
Original column order and row pointers remain reconstructible. Temporary
download/spool files are not a second authority. Failed or credential-echoing
responses produce sanitized private failure receipts, not unredacted evidence
in canonical tables. Content-addressing avoids duplicate BLOBs.

Nasdaq table responses are capped at 10,000 rows per page. Cursor termination
or a validated export determines transport completeness, not a guessed row
count. Export status and snapshot metadata must be checked; polling and file
download requests count toward the finite workload budget. A cursor walk is
not automatically a transactionally consistent snapshot of a changing remote
table.
[Nasdaq parameters](https://docs.data.nasdaq.com/docs/parameters-1),
[Nasdaq export protocol](https://docs.data.nasdaq.com/docs/in-depth-usage-1)

For mutable-source pagination, record coherence as unproven unless the
provider supplies a stable snapshot boundary or a validation protocol
establishes one. Initial universes and export generations are pinned. No
absence-based deletion follows from an incoherent acquisition.

For tables with a verified lastupdated filter, query changes across all
historical periods using an overlapping update-date watermark. Do not restrict
the incremental scan to the latest quarter: a restatement can affect old
periods. Commit the watermark only after every required partition succeeds.
Same-day corrections require repeated permitted windows and payload hashes,
not just a strictly-greater date predicate.

Do not assume every Sharadar table has lastupdated. For event, action,
insider or holdings tables without a usable update filter, use explicitly
bounded refreshed partitions and periodic complete reconciliations. These
reconciliations require their own finite workload/schedule authority.
Absence alone never deletes history. Tombstones require explicit retraction
or a complete authoritative scope with established removal semantics; until
that is proven, report absence as unconfirmed.

Refresh frequency determines how many intermediate provider states can be
observed. A later operational proposal should align acquisitions with the
actual Nasdaq delivery schedule and the desired revision sensitivity, with
explicit request, byte, polling and duration caps. No hidden retries or
unbounded export polling.

**9. Coverage and quality checks**

Each completion receipt should show table, entitlement result, schema version,
requested/returned fields, dates, dimension counts, active/delisted coverage,
unresolved mappings, partial partitions and update/checkpoint bounds. A
successful AAPL request is not evidence of access for every ticker.

Coverage states distinguish available, entitled-but-empty, not-entitled,
request-failed, parse-rejected and not-yet-checked. Unknown field definitions,
currency gaps and foreign-quarterly exclusions remain visible.

Validation should include:

- Exact replay writes zero rows anywhere in canonical acquisition state.
- AR and MR never collapse; multiple AR filings and Q4/FY collisions survive.
- Corrections, metadata-only changes and A-to-B-to-A reversions preserve
  predecessors and reproducible earlier results.
- A later MR capture cannot appear in an earlier strict/local cutoff.
- Date-only versus datetime cutoffs follow the existing completed_date matrix.
- Exact decimals, nulls, missing columns, unknown fields and scale conversions
  survive round-trip; no NaN/infinity or duplicate JSON keys.
- Ticker changes, reused symbols, multiple security classes, unresolved CIKs
  and ambiguous mappings retain source evidence.
- Conflicting duplicate source keys block the affected canonical publication.
- A row arriving through a broader capture gains membership without an
  unnecessary new value version.
- Cursor failures, changing remote pages, stale exports, rollback and crash
  recovery cannot advance a completed checkpoint.
- Archive limits, credential redaction, host restrictions and redirects keep
  credentials out of artifacts, logs and unintended destinations.
- Matching revenue, net income, assets, liabilities and cash-flow measures
  against selected SEC filings identifies definition differences explicitly.

Accounting identities are useful warnings with defined rounding tolerances,
not universal hard failures across every industry and source definition.
Negative earnings, ratios and restatement-driven quarterly values can be
valid. Full tests and fresh independent verification apply before a future
migration/shared-safety implementation is accepted, under the existing
[validation strategy](TEST_STRATEGY.md). They were not run for this document.

**10. Special rules for related tables**

Institutional holdings represent quarter-end positions; that date is not the
date the filing became knowable. Do not assign quarter-end availability or
invent filing timestamps when the feed omits them. Retain locally observed
versions and require additional filing evidence for historical strict as_of.
Keep securitytype in identity and preserve fund/put/call/debt distinctions.
Provider aggregate tables and local aggregations have separate lineage.
[Holdings schema and date filter](https://sharadar.com/docs/holdings)

Insider data needs both transaction date and filing date. Multiple transactions
on a filing/day must retain the provider row discriminator and full native
key; owner name and ticker alone are insufficient. Never merge an insider
person identifier with an issuer or institutional manager identifier.
[Insider schema](https://sharadar.com/docs/insiders)

Stock/fund price variants remain explicit. The documented OHLCV series is
split-adjusted, while closeadj additionally reflects distributions and
closeunadj is unadjusted. Provider-native fields remain unchanged; any
imputed OHLC series is a separate derived transformation. Later adjustments
can revise old price history, so price versions also need capture lineage.
[Stock prices](https://sharadar.com/docs/stocks),
[Fund prices](https://sharadar.com/docs/funds)

Current classifications and exchange labels cannot be applied to old dates
without historical evidence. Sharadar documents some historical SIC changes
through ACTIONS, but says the exchange field is the latest listing venue.
Index membership likewise needs its historical snapshot/change coverage,
not today's constituent list.
[Reference-data limitations](https://sharadar.com/docs/faqs),
[S&P500 history](https://sharadar.com/docs/sp500)

**11. Storage sizing and delivery sequence**

Measure actual row counts and compressed/uncompressed sizes during a bounded
preflight. Public sample ZIP sizes are not this account's storage estimate.
Budget independently for raw evidence, normalized row payloads, memberships,
indexes, staging and future corrections. The large daily-price and holdings
families may dominate total storage; six SF1 dimensions also materially expand
the baseline.

Start with SF1, table definitions, identity/reference data and corporate
actions, including all six dimensions from the first baseline. Add DAILY and
EVENTS next, then price/index coverage and the investor families according to
entitlements and measured size. Metrics requires its own Nasdaq availability
or delivery-channel decision. This order preserves broad field and
reporting-dimension coverage within the selected universe; it makes each
domain's result independently reviewable.

SQLite remains authoritative. Consider immutable Parquet projections only
after the existing benchmark and equivalence gate demonstrates a benefit;
collectors do not dual-write a competing authority.
[SQLite/Parquet decision](../adr/0007-sqlite-authority-derived-parquet.md)

**12. Integration decisions before implementation**

The reviewed working tree contains substantial concurrent/uncommitted work,
including company migration 0011 for transcript analysis. This design neither
edits it nor reserves the next ordinal. Reconcile the stable integrated
registry and migration head before allocating Sharadar migrations.

The remaining implementation inputs are concrete:

- Nasdaq metadata and account scope for each proposed table, including full
  SF1 history and non-AAPL access.
- Pinned Nasdaq field/key/scale contracts, especially where direct documentation
  uses different names or internally inconsistent labels.
- Measured baseline storage and a finite initial acquisition manifest.
- Confirmed removal semantics and revision/adjustment timing sufficient for any
  stronger historical-as_of promise.
- The exact optional recurring cadence, caps and reconciliation windows.

These are preflight inputs, not reasons to discard data or guess semantics.
The original design review used public documentation and made no additional
authenticated requests or operational-store accesses. During the later
universe-list task, three FMP constituent requests and one public IWM download
succeeded without retries, followed by selection of the user's CSV as the
authoritative list. One quiet immutable market read supplied the 519-equity
comparison. All subsequent list processing was offline. No selected-universe
Sharadar request, canonical write, scheduler action, migration edit or registry
change was performed.
